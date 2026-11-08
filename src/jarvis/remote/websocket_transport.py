"""Transporte WebSocket para o protocolo lógico do Remote Gateway."""

from __future__ import annotations

import ssl
import socket
import threading
from typing import Callable, Sequence

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import Server, ServerConnection, serve

from jarvis.remote.protocol import (
    BaseMessage,
    MAX_MESSAGE_SIZE,
    ProtocolError,
    parse_message,
    serialize_message,
)
from jarvis.remote.transport import (
    ClientInfo,
    ConnectionHandler,
    ReceiveResult,
    ReceiveStatus,
    TransportConnection,
    TransportServer,
)


class WebSocketTransportServer(TransportServer):
    """Servidor WebSocket que entrega as mesmas mensagens lógicas do TCP."""

    def __init__(
        self,
        host: str,
        port: int,
        certfile: str | None = None,
        keyfile: str | None = None,
        allow_lan: bool = False,
        *,
        allowed_origins: Sequence[str] = (),
    ) -> None:
        if bool(certfile) != bool(keyfile):
            raise ValueError("certfile e keyfile devem ser informados juntos")
        if not allow_lan and host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Exposição LAN requer allow_lan=True")
        self.host = host
        self.port = port
        self.allow_lan = allow_lan
        self.allowed_origins = tuple(allowed_origins)
        self._ssl_context: ssl.SSLContext | None = None
        if certfile and keyfile:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certfile, keyfile)
            self._ssl_context = context
        self._server: Server | None = None
        self._server_thread: threading.Thread | None = None
        self._connection_threads: set[threading.Thread] = set()
        self._handler: ConnectionHandler | None = None
        self._connections: set[WebSocketTransportConnection] = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()

    def start(self, handler: ConnectionHandler) -> None:
        with self._lock:
            if self.is_running:
                return
            server = serve(
                self._handle_socket,
                self.host,
                self.port,
                ssl=self._ssl_context,
                origins=[None, *self.allowed_origins],
                compression=None,
                ping_interval=None,
                close_timeout=1,
                max_size=MAX_MESSAGE_SIZE,
                max_queue=4,
                server_header=None,
            )
            self._handler = handler
            self._server = server
            server.socket.settimeout(0.25)
            self._stop_event.clear()
            self._server_thread = threading.Thread(
                target=self._accept_loop,
                args=(server,),
                name="jarvis-remote-websocket-server",
                daemon=True,
            )
            self._server_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            server = self._server
            self._server = None
            thread = self._server_thread
            self._server_thread = None
            connections = tuple(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        if server is not None:
            server.shutdown()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            connection_threads = tuple(self._connection_threads)
        for connection_thread in connection_threads:
            if connection_thread is not threading.current_thread():
                connection_thread.join(timeout=2.0)

    @property
    def is_running(self) -> bool:
        thread = self._server_thread
        return thread is not None and thread.is_alive()

    @property
    def bound_address(self) -> tuple[str, int] | None:
        server = self._server
        if server is None:
            return None
        host, port = server.socket.getsockname()[:2]
        return str(host), int(port)

    def _handle_socket(self, websocket: ServerConnection) -> None:
        address = websocket.remote_address
        if not isinstance(address, tuple) or len(address) < 2:
            websocket.close(code=1008, reason="Endereço remoto inválido")
            return
        host, port = str(address[0]), int(address[1])
        if host not in {"127.0.0.1", "::1"} and not self.allow_lan:
            websocket.close(code=1008, reason="Conexão LAN não permitida")
            return
        connection = WebSocketTransportConnection(
            websocket,
            ClientInfo((host, port), secure=self._ssl_context is not None),
            on_close=self._discard_connection,
        )
        with self._lock:
            if self._stop_event.is_set():
                connection.close()
                return
            self._connections.add(connection)
            handler = self._handler
        if handler is None:
            connection.close()
            return
        try:
            handler(connection, connection.client_info)
            connection.wait_closed()
        except Exception:
            connection.close()
        finally:
            self._discard_connection(connection)

    def _accept_loop(self, server: Server) -> None:
        while not self._stop_event.is_set():
            try:
                client_socket, address = server.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            thread = threading.Thread(
                target=self._run_connection_handler,
                args=(server, client_socket, address),
                name="jarvis-remote-websocket-connection",
                daemon=True,
            )
            with self._lock:
                if self._stop_event.is_set():
                    client_socket.close()
                    return
                self._connection_threads.add(thread)
            thread.start()

    def _run_connection_handler(
        self,
        server: Server,
        client_socket: socket.socket,
        address: object,
    ) -> None:
        try:
            server.handler(client_socket, address)
        finally:
            with self._lock:
                self._connection_threads.discard(threading.current_thread())

    def _discard_connection(
        self,
        connection: WebSocketTransportConnection,
    ) -> None:
        with self._lock:
            self._connections.discard(connection)


class WebSocketTransportConnection(TransportConnection):
    def __init__(
        self,
        websocket: ServerConnection,
        client_info: ClientInfo,
        *,
        on_close: Callable[[WebSocketTransportConnection], None] | None = None,
    ) -> None:
        self._websocket = websocket
        self._client_info = client_info
        self._on_close = on_close
        self._timeout: float | None = None
        self._closed = False
        self._closed_event = threading.Event()
        self._send_lock = threading.Lock()
        self._receive_lock = threading.Lock()
        self._state_lock = threading.RLock()

    def send(self, message: BaseMessage) -> bool:
        try:
            payload = serialize_message(message)[:-1].decode("utf-8")
        except (ProtocolError, UnicodeDecodeError):
            return False
        with self._send_lock:
            if self.is_closed:
                return False
            try:
                self._websocket.send(payload)
                return True
            except (ConnectionClosed, OSError):
                self.close()
                return False

    def receive(self) -> ReceiveResult:
        with self._receive_lock:
            if self.is_closed:
                return ReceiveResult(ReceiveStatus.CLOSED)
            try:
                payload = self._websocket.recv(timeout=self._timeout)
            except TimeoutError:
                return ReceiveResult(ReceiveStatus.TIMEOUT)
            except ConnectionClosed as error:
                too_large = any(
                    frame is not None and int(frame.code) == 1009
                    for frame in (error.rcvd, error.sent)
                )
                self.close()
                return ReceiveResult(
                    ReceiveStatus.TOO_LARGE if too_large else ReceiveStatus.CLOSED
                )
            except OSError:
                self.close()
                return ReceiveResult(ReceiveStatus.CLOSED)
            raw = payload.encode("utf-8") if isinstance(payload, str) else payload
            if len(raw) > MAX_MESSAGE_SIZE:
                self._close_too_large()
                return ReceiveResult(ReceiveStatus.TOO_LARGE)
            try:
                message = parse_message(raw)
            except ProtocolError:
                return ReceiveResult(ReceiveStatus.INVALID)
            return ReceiveResult(ReceiveStatus.MESSAGE, message)

    def set_timeout(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("Timeout deve ser positivo")
        with self._state_lock:
            if not self._closed:
                self._timeout = seconds

    def close(self) -> None:
        self._close(1000, "")

    def _close_too_large(self) -> None:
        self._close(1009, "Mensagem excede o limite")

    def _close(self, code: int, reason: str) -> None:
        callback = None
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._closed_event.set()
            callback = self._on_close
            self._on_close = None
        try:
            self._websocket.close(code=code, reason=reason)
        except (ConnectionClosed, OSError):
            pass
        if callback is not None:
            callback(self)

    def wait_closed(self) -> None:
        self._closed_event.wait()

    @property
    def is_closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def client_info(self) -> ClientInfo:
        return self._client_info
