from pathlib import Path
import socket
import time

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection as WebSocketClientConnection
from websockets.sync.client import connect as websocket_connect

from jarvis.application.state import AgentStateBus
from jarvis.application.telemetry import TelemetryBus
from jarvis.remote.auth import DeviceRegistry, PairingManager
from jarvis.remote.core_port import RemoteConfirmation
from jarvis.remote.gateway import RemoteGateway
from jarvis.remote.protocol import (
    AuthPair,
    AuthSuccess,
    AuthToken,
    BaseMessage,
    ChatMessage,
    ChatResponse,
    ConfirmationRequest,
    ConfirmationRespond,
    DevicePaired,
    DeviceRevoked,
    Error,
    MAX_MESSAGE_SIZE,
    Ping,
    Pong,
    parse_message,
    serialize_message,
)
from jarvis.remote.transport import TCPTransportServer
from jarvis.remote.websocket_transport import WebSocketTransportServer


class FakeCorePort:
    def __init__(self, response: str = "core response") -> None:
        self.response = response
        self.chat_calls: list[str] = []
        self.confirmation_calls: list[tuple[bool, str, str]] = []
        self.confirmation: RemoteConfirmation | None = None
        self.create_confirmation = False

    def process_chat(self, text: str) -> str:
        self.chat_calls.append(text)
        if self.create_confirmation:
            self.confirmation = RemoteConfirmation(
                session_id="core-session",
                challenge_id="core-challenge",
                action="critical_tool",
                target="target",
                risk="CRITICAL",
                prompt_text="Confirmar?",
                expires_at=time.monotonic() + 60,
                reminder_count=0,
            )
        return self.response

    def process_confirmation(
        self,
        *,
        confirmed: bool,
        session_id: str,
        challenge_id: str,
    ) -> str:
        self.confirmation_calls.append((confirmed, session_id, challenge_id))
        self.confirmation = None
        return "confirmed by core" if confirmed else "denied by core"

    def current_confirmation(self) -> RemoteConfirmation | None:
        return self.confirmation


class WebSocketClient:
    def __init__(self, uri: str) -> None:
        self.connection: WebSocketClientConnection = websocket_connect(uri)

    def send(self, message: BaseMessage) -> None:
        self.connection.send(message.to_json())

    def receive(self) -> BaseMessage:
        payload = self.connection.recv(timeout=2.0)
        assert isinstance(payload, str)
        return parse_message(payload.encode("utf-8"))

    def close(self) -> None:
        self.connection.close()


class TCPClient:
    def __init__(self, address: tuple[str, int]) -> None:
        self.connection = socket.create_connection(address, timeout=2.0)
        self.connection.settimeout(2.0)
        self.file = self.connection.makefile("rb")

    def send(self, message: BaseMessage) -> None:
        self.connection.sendall(serialize_message(message))

    def receive(self) -> BaseMessage:
        line = self.file.readline()
        assert line
        return parse_message(line.rstrip(b"\r\n"))

    def close(self) -> None:
        self.file.close()
        self.connection.close()


def start_gateway(
    tmp_path: Path,
    core: FakeCorePort,
    transport: str = "websocket",
) -> tuple[RemoteGateway, Path, Path, str | tuple[str, int]]:
    registry_path = tmp_path / "devices.json"
    pairing_path = tmp_path / "pairing.json"
    transport_server = (
        WebSocketTransportServer("127.0.0.1", 0)
        if transport == "websocket"
        else TCPTransportServer("127.0.0.1", 0)
    )
    gateway = RemoteGateway(
        core_port=core,
        state_bus=AgentStateBus(),
        telemetry_bus=TelemetryBus(),
        host="127.0.0.1",
        port=0,
        device_registry=DeviceRegistry(registry_path),
        pairing_manager=PairingManager(pairing_path),
        transport_server=transport_server,
    )
    gateway.start()
    address = gateway.bound_address
    assert address is not None
    endpoint: str | tuple[str, int] = (
        f"ws://{address[0]}:{address[1]}"
        if transport == "websocket"
        else address
    )
    return gateway, registry_path, pairing_path, endpoint


def pair(client: WebSocketClient | TCPClient, pairing_path: Path) -> DevicePaired:
    challenge = PairingManager(pairing_path).create_pairing("Phone")
    client.send(
        AuthPair(
            device_name="Phone",
            pairing_code=challenge.pairing_code,
            request_id="pair",
        )
    )
    response = client.receive()
    assert isinstance(response, DevicePaired)
    return response


def test_websocket_gateway_pairing_chat_ping_and_lifecycle(tmp_path: Path) -> None:
    core = FakeCorePort()
    gateway, _registry_path, pairing_path, endpoint = start_gateway(tmp_path, core)
    gateway.start()
    assert isinstance(endpoint, str)
    client = WebSocketClient(endpoint)
    try:
        paired = pair(client, pairing_path)
        client.send(ChatMessage(text="hello", request_id="chat"))
        response = client.receive()
        client.send(Ping(request_id="ping"))
        pong = client.receive()

        assert paired.token
        assert isinstance(response, ChatResponse)
        assert response.text == "core response"
        assert response.request_id == "chat"
        assert core.chat_calls == ["hello"]
        assert isinstance(pong, Pong)
        assert pong.request_id == "ping"
    finally:
        client.close()
        gateway.stop()
        gateway.stop()
    assert gateway.is_running is False


def test_websocket_gateway_authentication_and_reconnect(tmp_path: Path) -> None:
    gateway, _registry_path, pairing_path, endpoint = start_gateway(
        tmp_path,
        FakeCorePort(),
    )
    assert isinstance(endpoint, str)
    first = WebSocketClient(endpoint)
    paired = pair(first, pairing_path)
    first.close()
    second = WebSocketClient(endpoint)
    try:
        second.send(AuthToken(device_id=paired.device_id, token=paired.token))
        authenticated = second.receive()

        assert isinstance(authenticated, AuthSuccess)
        assert authenticated.device_id == paired.device_id
        assert not hasattr(authenticated, "token")
    finally:
        second.close()
        gateway.stop()


def test_websocket_gateway_requires_authentication(tmp_path: Path) -> None:
    gateway, _registry_path, _pairing_path, endpoint = start_gateway(
        tmp_path,
        FakeCorePort(),
    )
    assert isinstance(endpoint, str)
    client = WebSocketClient(endpoint)
    try:
        client.send(ChatMessage(text="hello"))
        response = client.receive()

        assert isinstance(response, Error)
        assert response.code == "unauthenticated"
    finally:
        client.close()
        gateway.stop()


def test_websocket_confirmation_uses_original_core_ids(tmp_path: Path) -> None:
    core = FakeCorePort()
    core.create_confirmation = True
    gateway, _registry_path, pairing_path, endpoint = start_gateway(tmp_path, core)
    assert isinstance(endpoint, str)
    client = WebSocketClient(endpoint)
    try:
        pair(client, pairing_path)
        client.send(ChatMessage(text="critical", request_id="chat"))
        messages = [client.receive(), client.receive()]
        confirmation = next(
            message for message in messages if isinstance(message, ConfirmationRequest)
        )
        client.send(
            ConfirmationRespond(
                session_id=confirmation.session_id,
                challenge_id=confirmation.challenge_id,
                confirmed=True,
                request_id="confirmation",
            )
        )
        response = client.receive()

        assert isinstance(response, ChatResponse)
        assert response.text == "confirmed by core"
        assert core.confirmation_calls == [
            (True, "core-session", "core-challenge")
        ]
    finally:
        client.close()
        gateway.stop()


def test_websocket_disconnect_denies_pending_confirmation(tmp_path: Path) -> None:
    core = FakeCorePort()
    core.create_confirmation = True
    gateway, _registry_path, pairing_path, endpoint = start_gateway(tmp_path, core)
    assert isinstance(endpoint, str)
    client = WebSocketClient(endpoint)
    pair(client, pairing_path)
    client.send(ChatMessage(text="critical"))
    messages = [client.receive(), client.receive()]
    assert any(isinstance(message, ConfirmationRequest) for message in messages)

    client.close()
    deadline = time.monotonic() + 2.0
    while not core.confirmation_calls and time.monotonic() < deadline:
        time.sleep(0.01)
    gateway.stop()

    assert core.confirmation_calls == [
        (False, "core-session", "core-challenge")
    ]


def test_websocket_external_revocation_disconnects_client(tmp_path: Path) -> None:
    gateway, registry_path, pairing_path, endpoint = start_gateway(
        tmp_path,
        FakeCorePort(),
    )
    assert isinstance(endpoint, str)
    client = WebSocketClient(endpoint)
    try:
        paired = pair(client, pairing_path)
        DeviceRegistry(registry_path).revoke(paired.device_id)

        response = client.receive()

        assert isinstance(response, DeviceRevoked)
        assert response.device_id == paired.device_id
    finally:
        client.close()
        gateway.stop()


def test_websocket_gateway_rejects_malformed_message_and_cleans_session(
    tmp_path: Path,
) -> None:
    gateway, _registry_path, pairing_path, endpoint = start_gateway(
        tmp_path,
        FakeCorePort(),
    )
    assert isinstance(endpoint, str)
    client = WebSocketClient(endpoint)
    pair(client, pairing_path)
    assert gateway.session_manager.list_sessions()

    client.connection.send("not-json")
    response = client.receive()

    assert isinstance(response, Error)
    assert response.code == "invalid_message"
    with pytest.raises(ConnectionClosed):
        client.connection.recv(timeout=2.0)
    deadline = time.monotonic() + 2.0
    while gateway.session_manager.list_sessions() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gateway.session_manager.list_sessions() == []
    client.close()
    gateway.stop()


def test_websocket_gateway_closes_oversize_payload(tmp_path: Path) -> None:
    gateway, _registry_path, _pairing_path, endpoint = start_gateway(
        tmp_path,
        FakeCorePort(),
    )
    assert isinstance(endpoint, str)
    client = WebSocketClient(endpoint)
    try:
        client.connection.send("x" * (MAX_MESSAGE_SIZE + 1))
        with pytest.raises(ConnectionClosed) as closed:
            client.connection.recv(timeout=2.0)
        assert any(
            frame is not None and int(frame.code) == 1009
            for frame in (closed.value.rcvd, closed.value.sent)
        )
    finally:
        client.close()
        gateway.stop()


def test_websocket_preserves_core_security_block_result(tmp_path: Path) -> None:
    core = FakeCorePort("security_blocked: kill switch acionado")
    gateway, _registry_path, pairing_path, endpoint = start_gateway(tmp_path, core)
    assert isinstance(endpoint, str)
    client = WebSocketClient(endpoint)
    try:
        pair(client, pairing_path)
        client.send(ChatMessage(text="move pointer"))
        response = client.receive()

        assert isinstance(response, ChatResponse)
        assert response.text == "security_blocked: kill switch acionado"
    finally:
        client.close()
        gateway.stop()


def test_tcp_and_websocket_gateway_behavior_is_equivalent(tmp_path: Path) -> None:
    transcripts = []
    for transport in ("tcp", "websocket"):
        core = FakeCorePort()
        gateway, _registry_path, pairing_path, endpoint = start_gateway(
            tmp_path / transport,
            core,
            transport,
        )
        client = (
            WebSocketClient(endpoint)
            if isinstance(endpoint, str)
            else TCPClient(endpoint)
        )
        try:
            paired = pair(client, pairing_path)
            client.send(ChatMessage(text="same request", request_id="chat"))
            response = client.receive()
            client.send(Ping(request_id="ping"))
            pong = client.receive()
            transcripts.append(
                (
                    type(response),
                    getattr(response, "text", None),
                    response.request_id,
                    type(pong),
                    pong.request_id,
                    bool(paired.token),
                    core.chat_calls,
                )
            )
        finally:
            client.close()
            gateway.stop()

    assert transcripts[0] == transcripts[1]
