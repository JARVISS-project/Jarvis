import queue

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from jarvis.remote.protocol import MAX_MESSAGE_SIZE, Ping, Pong, parse_message
from jarvis.remote.transport import ReceiveStatus
from jarvis.remote.websocket_transport import WebSocketTransportServer


def websocket_uri(server: WebSocketTransportServer) -> str:
    address = server.bound_address
    assert address is not None
    return f"ws://{address[0]}:{address[1]}"


def test_websocket_server_lifecycle_is_idempotent() -> None:
    server = WebSocketTransportServer("127.0.0.1", 0)
    server.start(lambda _connection, _info: None)
    address = server.bound_address
    server.start(lambda _connection, _info: None)

    assert server.is_running is True
    assert address is not None and address[1] > 0

    server.stop()
    server.stop()
    assert server.is_running is False

    server.start(lambda _connection, _info: None)
    assert server.is_running is True
    server.stop()


def test_websocket_connection_uses_typed_logical_messages() -> None:
    accepted = queue.Queue()
    server = WebSocketTransportServer("127.0.0.1", 0)
    server.start(lambda connection, _info: accepted.put(connection))
    client = connect(websocket_uri(server))
    try:
        connection = accepted.get(timeout=1.0)
        connection.set_timeout(0.5)
        client.send(Ping(request_id="request").to_json())

        received = connection.receive()

        assert received.status is ReceiveStatus.MESSAGE
        assert isinstance(received.message, Ping)
        assert received.message.request_id == "request"
        assert connection.send(Pong(request_id="request")) is True
        response = client.recv(timeout=1.0)
        assert isinstance(response, str)
        assert isinstance(parse_message(response.encode("utf-8")), Pong)
    finally:
        client.close()
        server.stop()


def test_websocket_connection_reports_malformed_message() -> None:
    accepted = queue.Queue()
    server = WebSocketTransportServer("127.0.0.1", 0)
    server.start(lambda connection, _info: accepted.put(connection))
    client = connect(websocket_uri(server))
    try:
        connection = accepted.get(timeout=1.0)
        connection.set_timeout(0.5)
        client.send("not-json")

        assert connection.receive().status is ReceiveStatus.INVALID
    finally:
        client.close()
        server.stop()


def test_websocket_connection_closes_oversize_payload() -> None:
    accepted = queue.Queue()
    server = WebSocketTransportServer("127.0.0.1", 0)
    server.start(lambda connection, _info: accepted.put(connection))
    client = connect(websocket_uri(server), max_size=MAX_MESSAGE_SIZE)
    try:
        connection = accepted.get(timeout=1.0)
        connection.set_timeout(1.0)
        client.send("x" * (MAX_MESSAGE_SIZE + 1))

        result = connection.receive()

        assert result.status is ReceiveStatus.TOO_LARGE
        assert connection.is_closed is True
        with pytest.raises(ConnectionClosed) as closed:
            client.recv(timeout=1.0)
        assert any(
            frame is not None and int(frame.code) == 1009
            for frame in (closed.value.rcvd, closed.value.sent)
        )
    finally:
        client.close()
        server.stop()


def test_websocket_server_stop_closes_connections_and_handlers() -> None:
    accepted = queue.Queue()
    server = WebSocketTransportServer("127.0.0.1", 0)
    server.start(lambda connection, _info: accepted.put(connection))
    client = connect(websocket_uri(server))
    connection = accepted.get(timeout=1.0)

    server.stop()

    assert connection.is_closed is True
    with pytest.raises(ConnectionClosed):
        client.recv(timeout=1.0)
    client.close()


def test_websocket_rejects_unlisted_browser_origin() -> None:
    server = WebSocketTransportServer("127.0.0.1", 0)
    server.start(lambda _connection, _info: None)
    try:
        with pytest.raises(InvalidStatus):
            connect(websocket_uri(server), origin="https://untrusted.example")
    finally:
        server.stop()


def test_websocket_accepts_explicit_browser_origin() -> None:
    accepted = queue.Queue()
    origin = "http://127.0.0.1:3000"
    server = WebSocketTransportServer(
        "127.0.0.1",
        0,
        allowed_origins=(origin,),
    )
    server.start(lambda connection, _info: accepted.put(connection))
    client = connect(websocket_uri(server), origin=origin)
    try:
        assert accepted.get(timeout=1.0).client_info.remote_addr[0] == "127.0.0.1"
    finally:
        client.close()
        server.stop()
