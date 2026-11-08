"""Contrato de transporte e implementação TCP/NDJSON do Remote Gateway."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import socket
import ssl
import threading
from typing import Callable

from jarvis.remote.protocol import (
    BaseMessage,
    MAX_MESSAGE_SIZE,
    ProtocolError,
    parse_message,
    serialize_message,
)


@dataclass(frozen=True, slots=True)
class ClientInfo:
    remote_addr: tuple[str, int]
    secure: bool = False


class ReceiveStatus(Enum):
    MESSAGE = "message"
    TIMEOUT = "timeout"
    CLOSED = "closed"
    INVALID = "invalid"
    TOO_LARGE = "too_large"


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    status: ReceiveStatus
    message: BaseMessage | None = None


ConnectionHandler = Callable[["TransportConnection", ClientInfo], None]


class TransportServer(ABC):
    @abstractmethod
    def start(self, handler: ConnectionHandler) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def is_running(self) -> bool: ...


class TransportConnection(ABC):
    @abstractmethod
    def send(self, message: BaseMessage) -> bool: ...

    @abstractmethod
    def receive(self) -> ReceiveResult: ...

    @abstractmethod
    def set_timeout(self, seconds: float) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_closed(self) -> bool: ...

    @property
    @abstractmethod
    def client_info(self) -> ClientInfo: ...


class TCPTransportServer(TransportServer):
    """Servidor TCP que delega somente mensagens lógicas ao Gateway."""

    def __init__(
        self,
        host: str,
        port: int,
        certfile: str | None = None,
        keyfile: str | None = None,
        allow_lan: bool = False,
    ) -> None:
        if bool(certfile) != bool(keyfile):
            raise ValueError("certfile e keyfile devem ser informados juntos")
        if not allow_lan and host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Exposição LAN requer allow_lan=True")
        self.host = host
        self.port = port
        self.allow_lan = allow_lan
        self._ssl_context: ssl.SSLContext | None = None
        if certfile and keyfile:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certfile, keyfile)
            self._ssl_context = context
        self._server_socket: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._handler: ConnectionHandler | None = None
        self._connections: set[TCPTransportConnection] = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()

    def start(self, handler: ConnectionHandler) -> None:
        with self._lock:
            if self.is_running:
                return
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server_socket.bind((self.host, self.port))
                server_socket.listen(16)
                server_socket.settimeout(0.25)
            except Exception:
                server_socket.close()
                raise
            self._handler = handler
            self._server_socket = server_socket
            self._stop_event.clear()
            self._server_thread = threading.Thread(
                target=self._accept_loop,
                name="jarvis-remote-tcp-server",
                daemon=True,
            )
            self._server_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            server_socket = self._server_socket
            self._server_socket = None
            thread = self._server_thread
            self._server_thread = None
            connections = list(self._connections)
            self._connections.clear()
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass
        for connection in connections:
            connection.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    @property
    def is_running(self) -> bool:
        thread = self._server_thread
        return thread is not None and thread.is_alive()

    @property
    def bound_address(self) -> tuple[str, int] | None:
        server_socket = self._server_socket
        if server_socket is None:
            return None
        host, port = server_socket.getsockname()[:2]
        return str(host), int(port)

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            server_socket = self._server_socket
            if server_socket is None:
                return
            try:
                client_socket, address = server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if address[0] not in {"127.0.0.1", "::1"} and not self.allow_lan:
                client_socket.close()
                continue
            secure = False
            if self._ssl_context is not None:
                try:
                    client_socket = self._ssl_context.wrap_socket(
                        client_socket,
                        server_side=True,
                    )
                    secure = True
                except (OSError, ssl.SSLError):
                    client_socket.close()
                    continue
            connection = TCPTransportConnection(
                client_socket,
                ClientInfo((str(address[0]), int(address[1])), secure=secure),
                on_close=self._discard_connection,
            )
            with self._lock:
                if self._stop_event.is_set():
                    connection.close()
                    return
                self._connections.add(connection)
                handler = self._handler
            if handler is not None:
                try:
                    handler(connection, connection.client_info)
                except Exception:
                    connection.close()

    def _discard_connection(self, connection: "TCPTransportConnection") -> None:
        with self._lock:
            self._connections.discard(connection)


class TCPTransportConnection(TransportConnection):
    def __init__(
        self,
        sock: socket.socket,
        client_info: ClientInfo,
        *,
        on_close: Callable[["TCPTransportConnection"], None] | None = None,
    ) -> None:
        self._socket = sock
        self._client_info = client_info
        self._on_close = on_close
        self._buffer = bytearray()
        self._closed = False
        self._send_lock = threading.Lock()
        self._receive_lock = threading.Lock()
        self._state_lock = threading.RLock()

    def send(self, message: BaseMessage) -> bool:
        try:
            payload = serialize_message(message)
        except ProtocolError:
            return False
        with self._send_lock:
            if self.is_closed:
                return False
            try:
                self._socket.sendall(payload)
                return True
            except OSError:
                self.close()
                return False

    def receive(self) -> ReceiveResult:
        with self._receive_lock:
            if self.is_closed:
                return ReceiveResult(ReceiveStatus.CLOSED)
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                return self._consume_line(newline)
            if len(self._buffer) > MAX_MESSAGE_SIZE:
                self.close()
                return ReceiveResult(ReceiveStatus.TOO_LARGE)
            try:
                chunk = self._socket.recv(
                    min(4096, MAX_MESSAGE_SIZE + 1 - len(self._buffer))
                )
            except socket.timeout:
                return ReceiveResult(ReceiveStatus.TIMEOUT)
            except OSError:
                self.close()
                return ReceiveResult(ReceiveStatus.CLOSED)
            if not chunk:
                self.close()
                return ReceiveResult(ReceiveStatus.CLOSED)
            self._buffer.extend(chunk)
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                return self._consume_line(newline)
            if len(self._buffer) > MAX_MESSAGE_SIZE:
                self.close()
                return ReceiveResult(ReceiveStatus.TOO_LARGE)
            return ReceiveResult(ReceiveStatus.TIMEOUT)

    def _consume_line(self, newline: int) -> ReceiveResult:
        line = bytes(self._buffer[:newline])
        del self._buffer[: newline + 1]
        if line.endswith(b"\r"):
            line = line[:-1]
        if len(line) > MAX_MESSAGE_SIZE:
            self.close()
            return ReceiveResult(ReceiveStatus.TOO_LARGE)
        try:
            message = parse_message(line)
        except ProtocolError:
            return ReceiveResult(ReceiveStatus.INVALID)
        return ReceiveResult(ReceiveStatus.MESSAGE, message)

    def set_timeout(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("Timeout deve ser positivo")
        with self._state_lock:
            if not self._closed:
                self._socket.settimeout(seconds)

    def close(self) -> None:
        callback = None
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._socket.close()
            except OSError:
                pass
            callback = self._on_close
            self._on_close = None
        if callback is not None:
            callback(self)

    @property
    def is_closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def client_info(self) -> ClientInfo:
        return self._client_info
