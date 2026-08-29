from pathlib import Path
import socket
import time

from jarvis.application.state import AgentStateBus
from jarvis.application.telemetry import TelemetryBus
from jarvis.remote.auth import DeviceRegistry, PairingManager
from jarvis.remote.core_port import RemoteConfirmation
from jarvis.remote.gateway import RemoteGateway
from jarvis.remote.protocol import (
    AuthPair,
    AuthSuccess,
    AuthToken,
    ChatMessage,
    ChatResponse,
    ConfirmationRequest,
    ConfirmationRespond,
    DevicePaired,
    DeviceRevoked,
    Error,
    Ping,
    Pong,
    parse_message,
    serialize_message,
)


class FakeCorePort:
    def __init__(self, response: str = "core response") -> None:
        self.response = response
        self.chat_calls = []
        self.confirmation_calls = []
        self.confirmation = None
        self.create_confirmation = False

    def process_chat(self, text: str) -> str:
        self.chat_calls.append(text)
        if self.create_confirmation:
            self.confirmation = RemoteConfirmation(
                session_id="confirmation-session",
                challenge_id="confirmation-challenge",
                action="critical_tool",
                target="target",
                risk="CRITICAL",
                prompt_text="Confirmar?",
                expires_at=time.monotonic() + 60,
                reminder_count=0,
            )
        return self.response

    def process_confirmation(self, *, confirmed, session_id, challenge_id):
        self.confirmation_calls.append((confirmed, session_id, challenge_id))
        self.confirmation = None
        return "confirmed by core" if confirmed else "denied by core"

    def current_confirmation(self):
        return self.confirmation


def start_gateway(tmp_path: Path, core: FakeCorePort):
    registry_path = tmp_path / "devices.json"
    pairing_path = tmp_path / "pairing.json"
    gateway = RemoteGateway(
        core_port=core,
        state_bus=AgentStateBus(),
        telemetry_bus=TelemetryBus(),
        host="127.0.0.1",
        port=0,
        device_registry=DeviceRegistry(registry_path),
        pairing_manager=PairingManager(pairing_path),
    )
    gateway.start()
    address = gateway.bound_address
    assert address is not None
    return gateway, registry_path, pairing_path, address


def connect(address):
    client = socket.create_connection(address, timeout=2.0)
    client.settimeout(2.0)
    return client, client.makefile("rb")


def send(client: socket.socket, message) -> None:
    client.sendall(serialize_message(message))


def receive(file):
    line = file.readline()
    assert line
    return parse_message(line.rstrip(b"\r\n"))


def pair(client, file, pairing_path: Path):
    challenge = PairingManager(pairing_path).create_pairing("Phone")
    send(
        client,
        AuthPair(
            device_name="Phone",
            pairing_code=challenge.pairing_code,
            request_id="pair",
        ),
    )
    paired = receive(file)
    assert isinstance(paired, DevicePaired)
    return paired


def test_gateway_real_tcp_pair_auth_chat_and_idempotent_lifecycle(tmp_path: Path) -> None:
    core = FakeCorePort()
    gateway, _registry_path, pairing_path, address = start_gateway(tmp_path, core)
    gateway.start()
    client, file = connect(address)
    try:
        paired = pair(client, file, pairing_path)
        send(client, ChatMessage(text="hello", request_id="chat"))
        response = receive(file)

        assert isinstance(response, ChatResponse)
        assert response.text == "core response"
        assert response.request_id == "chat"
        assert core.chat_calls == ["hello"]

        send(client, Ping(request_id="ping"))
        pong = receive(file)
        assert isinstance(pong, Pong)
        assert pong.request_id == "ping"
        assert paired.token
    finally:
        file.close()
        client.close()
        gateway.stop()
        gateway.stop()
    assert gateway.is_running is False


def test_gateway_reconnect_auth_does_not_echo_token(tmp_path: Path) -> None:
    gateway, _registry_path, pairing_path, address = start_gateway(
        tmp_path,
        FakeCorePort(),
    )
    first, first_file = connect(address)
    paired = pair(first, first_file, pairing_path)
    first_file.close()
    first.close()
    second, second_file = connect(address)
    try:
        send(
            second,
            AuthToken(device_id=paired.device_id, token=paired.token),
        )
        authenticated = receive(second_file)

        assert isinstance(authenticated, AuthSuccess)
        assert authenticated.device_id == paired.device_id
        assert not hasattr(authenticated, "token")
    finally:
        second_file.close()
        second.close()
        gateway.stop()


def test_gateway_requires_authentication_before_chat(tmp_path: Path) -> None:
    gateway, _registry_path, _pairing_path, address = start_gateway(
        tmp_path,
        FakeCorePort(),
    )
    client, file = connect(address)
    try:
        send(client, ChatMessage(text="hello"))
        response = receive(file)
        assert isinstance(response, Error)
        assert response.code == "unauthenticated"
    finally:
        file.close()
        client.close()
        gateway.stop()


def test_gateway_confirmation_returns_to_original_core_session(tmp_path: Path) -> None:
    core = FakeCorePort()
    core.create_confirmation = True
    gateway, _registry_path, pairing_path, address = start_gateway(tmp_path, core)
    client, file = connect(address)
    try:
        pair(client, file, pairing_path)
        send(client, ChatMessage(text="critical"))
        messages = [receive(file), receive(file)]
        confirmation = next(
            item for item in messages if isinstance(item, ConfirmationRequest)
        )
        send(
            client,
            ConfirmationRespond(
                session_id=confirmation.session_id,
                challenge_id=confirmation.challenge_id,
                confirmed=True,
            ),
        )
        response = receive(file)

        assert isinstance(response, ChatResponse)
        assert response.text == "confirmed by core"
        assert core.confirmation_calls == [
            (True, "confirmation-session", "confirmation-challenge")
        ]
    finally:
        file.close()
        client.close()
        gateway.stop()


def test_external_revocation_disconnects_active_client(tmp_path: Path) -> None:
    gateway, registry_path, pairing_path, address = start_gateway(
        tmp_path,
        FakeCorePort(),
    )
    client, file = connect(address)
    try:
        paired = pair(client, file, pairing_path)
        DeviceRegistry(registry_path).revoke(paired.device_id)

        message = receive(file)

        assert isinstance(message, DeviceRevoked)
        assert message.device_id == paired.device_id
    finally:
        file.close()
        client.close()
        gateway.stop()


def test_gateway_preserves_core_security_block_result(tmp_path: Path) -> None:
    core = FakeCorePort(response="security_blocked: kill switch acionado")
    gateway, _registry_path, pairing_path, address = start_gateway(tmp_path, core)
    client, file = connect(address)
    try:
        pair(client, file, pairing_path)
        send(client, ChatMessage(text="move pointer"))
        response = receive(file)

        assert isinstance(response, ChatResponse)
        assert response.text == "security_blocked: kill switch acionado"
    finally:
        file.close()
        client.close()
        gateway.stop()
