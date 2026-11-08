import socket
import time

from jarvis.remote.protocol import ChatMessage, MAX_MESSAGE_SIZE, Ping, serialize_message
from jarvis.remote.transport import (
    ClientInfo,
    ReceiveStatus,
    TCPTransportConnection,
    TCPTransportServer,
)


def connection_pair() -> tuple[socket.socket, TCPTransportConnection]:
    client, server = socket.socketpair()
    connection = TCPTransportConnection(server, ClientInfo(("127.0.0.1", 1)))
    connection.set_timeout(0.05)
    return client, connection


def test_tcp_connection_reassembles_partial_frame() -> None:
    client, connection = connection_pair()
    try:
        payload = serialize_message(ChatMessage(text="partial"))
        client.sendall(payload[:5])
        assert connection.receive().status is ReceiveStatus.TIMEOUT
        client.sendall(payload[5:])

        result = connection.receive()

        assert result.status is ReceiveStatus.MESSAGE
        assert result.message == ChatMessage(
            text="partial",
            timestamp=result.message.timestamp,  # type: ignore[union-attr]
        )
    finally:
        client.close()
        connection.close()


def test_tcp_connection_preserves_second_buffered_frame() -> None:
    client, connection = connection_pair()
    try:
        client.sendall(serialize_message(Ping(request_id="1")) + serialize_message(Ping(request_id="2")))

        first = connection.receive()
        second = connection.receive()

        assert first.message is not None and first.message.request_id == "1"
        assert second.message is not None and second.message.request_id == "2"
    finally:
        client.close()
        connection.close()


def test_tcp_connection_closes_oversize_buffer_before_newline() -> None:
    client, connection = connection_pair()
    try:
        client.sendall(b"x" * (MAX_MESSAGE_SIZE + 1))
        status = ReceiveStatus.TIMEOUT
        while status is ReceiveStatus.TIMEOUT:
            status = connection.receive().status

        assert status is ReceiveStatus.TOO_LARGE
        assert connection.is_closed is True
    finally:
        client.close()
        connection.close()


def test_tcp_connection_reports_invalid_and_closed_frames() -> None:
    client, connection = connection_pair()
    client.sendall(b"not-json\n")
    assert connection.receive().status is ReceiveStatus.INVALID
    client.close()
    assert connection.receive().status is ReceiveStatus.CLOSED


def test_tcp_server_lifecycle_is_idempotent() -> None:
    server = TCPTransportServer("127.0.0.1", 0)
    server.start(lambda _connection, _info: None)
    address = server.bound_address
    server.start(lambda _connection, _info: None)

    assert server.is_running is True
    assert address is not None and address[1] > 0

    server.stop()
    server.stop()
    assert server.is_running is False


def test_tcp_server_accepts_real_loopback_connection() -> None:
    accepted = []
    server = TCPTransportServer("127.0.0.1", 0)
    server.start(lambda connection, info: accepted.append((connection, info)))
    try:
        address = server.bound_address
        assert address is not None
        client = socket.create_connection(address, timeout=1.0)
        try:
            deadline = time.monotonic() + 1.0
            while not accepted and time.monotonic() < deadline:
                time.sleep(0.01)
            assert accepted
            assert accepted[0][1].remote_addr[0] == "127.0.0.1"
        finally:
            client.close()
    finally:
        server.stop()
