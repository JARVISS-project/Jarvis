"""Remote Gateway autenticado e sem autoridade de execução."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import threading
import time

from jarvis.application.state import AgentStateBus
from jarvis.application.telemetry import TelemetryBus
from jarvis.remote.auth import (
    Device,
    DeviceRegistry,
    PairingManager,
    TokenAuthenticator,
    generate_token,
    hash_token,
)
from jarvis.remote.bridge import RemoteGatewayBridge
from jarvis.remote.core_port import RemoteCorePort
from jarvis.remote.protocol import (
    AuthFailed,
    AuthPair,
    AuthSuccess,
    AuthToken,
    BaseMessage,
    ChatMessage,
    ConfirmationRespond,
    DevicePaired,
    DeviceRevoked,
    Error,
    Ping,
    Pong,
    StateSubscribe,
    TelemetrySubscribe,
)
from jarvis.remote.sessions import RateLimiter, SessionManager
from jarvis.remote.transport import (
    ClientInfo,
    ReceiveStatus,
    TCPTransportServer,
    TransportConnection,
    TransportServer,
)
from jarvis.security.audit import AuditLogger


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766


@dataclass(slots=True)
class ClientConnection:
    transport: TransportConnection
    peer_key: str
    session_id: str = ""
    device_id: str | None = None
    authenticated: bool = False
    last_ping: float = 0.0


class RemoteGateway:
    def __init__(
        self,
        core_port: RemoteCorePort,
        state_bus: AgentStateBus,
        telemetry_bus: TelemetryBus,
        audit: AuditLogger | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        certfile: str | None = None,
        keyfile: str | None = None,
        allow_lan: bool = False,
        *,
        device_registry: DeviceRegistry | None = None,
        pairing_manager: PairingManager | None = None,
        transport_server: TransportServer | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        if not allow_lan and host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Exposição LAN requer allow_lan=True")
        self.host = host
        self.port = port
        self.audit = audit
        self.device_registry = device_registry or DeviceRegistry(audit=audit)
        self.pairing_manager = pairing_manager or PairingManager(audit=audit)
        self.token_auth = TokenAuthenticator(self.device_registry)
        self.session_manager = session_manager or SessionManager(
            max_sessions_per_device=3,
            session_timeout_seconds=3600,
        )
        self.pairing_limiter = RateLimiter(5, 300)
        self.auth_limiter = RateLimiter(20, 60)
        self.chat_limiter = RateLimiter(60, 60)
        self.confirmation_limiter = RateLimiter(30, 60)
        self.bridge = RemoteGatewayBridge(
            core_port,
            state_bus,
            telemetry_bus,
            audit_logger=audit,
        )
        self._transport_server = transport_server or TCPTransportServer(
            host=host,
            port=port,
            certfile=certfile,
            keyfile=keyfile,
            allow_lan=allow_lan,
        )
        self._clients: dict[TransportConnection, ClientConnection] = {}
        self._client_threads: set[threading.Thread] = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._cleanup_thread: threading.Thread | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running and self._transport_server.is_running

    @property
    def bound_address(self) -> tuple[str, int] | None:
        address = getattr(self._transport_server, "bound_address", None)
        return address if isinstance(address, tuple) else None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self.bridge.start()
            self._running = True
            try:
                self._transport_server.start(self._on_new_connection)
            except Exception:
                self._running = False
                self.bridge.stop()
                raise
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop,
                name="jarvis-remote-cleanup",
                daemon=True,
            )
            self._cleanup_thread.start()
        self._audit_event("gateway", "server", True, "started")

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            cleanup_thread = self._cleanup_thread
            self._cleanup_thread = None
            clients = tuple(self._clients.values())
        self._transport_server.stop()
        for client in clients:
            self._disconnect_client(client)
        if cleanup_thread is not None and cleanup_thread is not threading.current_thread():
            cleanup_thread.join(timeout=2.0)
        with self._lock:
            threads = tuple(self._client_threads)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self.bridge.stop()
        self._audit_event("gateway", "server", True, "stopped")

    def _on_new_connection(
        self,
        transport: TransportConnection,
        client_info: ClientInfo,
    ) -> None:
        with self._lock:
            if not self._running or self._stop_event.is_set():
                transport.close()
                return
            client = ClientConnection(
                transport=transport,
                peer_key=client_info.remote_addr[0],
                last_ping=time.monotonic(),
            )
            self._clients[transport] = client
            thread = threading.Thread(
                target=self._handle_client,
                args=(client,),
                name=(
                    "jarvis-remote-client-"
                    f"{client_info.remote_addr[0]}:{client_info.remote_addr[1]}"
                ),
                daemon=True,
            )
            self._client_threads.add(thread)
        transport.set_timeout(0.5)
        thread.start()

    def _handle_client(self, client: ClientConnection) -> None:
        handshake_deadline = time.monotonic() + 30.0
        try:
            while not self._stop_event.is_set():
                result = client.transport.receive()
                if result.status is ReceiveStatus.TIMEOUT:
                    if not client.authenticated and time.monotonic() >= handshake_deadline:
                        self._send_error(client, "auth_timeout", "Autenticação expirou.")
                        return
                    if client.authenticated and not self._session_is_valid(client):
                        return
                    continue
                if result.status is ReceiveStatus.CLOSED:
                    return
                if result.status is ReceiveStatus.TOO_LARGE:
                    return
                if result.status is ReceiveStatus.INVALID:
                    self._send_error(client, "invalid_message", "Mensagem inválida.")
                    return
                if result.message is None:
                    return
                if client.authenticated and not self._session_is_valid(client):
                    return
                self._process_message(client, result.message)
        finally:
            self._disconnect_client(client)
            with self._lock:
                self._client_threads.discard(threading.current_thread())

    def _session_is_valid(self, client: ClientConnection) -> bool:
        if not client.device_id or not client.session_id:
            return False
        try:
            self.device_registry.refresh()
        except Exception:
            self._audit_event("registry", "devices", False, "reload_failed")
            return False
        device = self.device_registry.get(client.device_id)
        if device is None or device.revoked:
            self._send(client, DeviceRevoked(device_id=client.device_id))
            return False
        return self.session_manager.get_session(client.session_id) is not None

    def _process_message(self, client: ClientConnection, message: BaseMessage) -> None:
        if isinstance(message, Ping):
            client.last_ping = time.monotonic()
            if client.session_id:
                self.session_manager.update_activity(client.session_id)
            self._send(client, Pong(request_id=message.request_id))
            return
        if isinstance(message, AuthPair):
            if client.authenticated:
                self._send_error(client, "already_authenticated", "Sessão já autenticada.")
            elif self.pairing_limiter.check(f"{client.peer_key}:pair"):
                self._handle_pair(client, message)
            else:
                self._send_error(client, "rate_limited", "Muitas tentativas de pairing.")
            return
        if isinstance(message, AuthToken):
            if client.authenticated:
                self._send_error(client, "already_authenticated", "Sessão já autenticada.")
            elif self.auth_limiter.check(f"{client.peer_key}:auth"):
                self._handle_token_auth(client, message)
            else:
                self._send_error(client, "rate_limited", "Muitas tentativas de autenticação.")
            return
        if not client.authenticated:
            self._send_error(client, "unauthenticated", "Autenticação necessária.")
            return
        self.session_manager.update_activity(client.session_id)
        device = self.device_registry.get(client.device_id or "")
        if device is None:
            return
        permission = self._required_permission(message)
        if permission is None:
            self._send_error(client, "unsupported", "Tipo de mensagem não suportado.")
            return
        if permission not in device.permissions:
            self._send_error(client, "forbidden", "Permissão insuficiente.")
            return
        limiter = (
            self.confirmation_limiter
            if isinstance(message, ConfirmationRespond)
            else self.chat_limiter if isinstance(message, ChatMessage) else None
        )
        if limiter is not None and not limiter.check(
            f"{client.device_id}:{permission}"
        ):
            self._send_error(client, "rate_limited", "Limite de mensagens excedido.")
            return
        self.bridge.handle_message(client.session_id, message)
        if isinstance(message, StateSubscribe):
            self._update_subscription(client, "state", message.subscribe)
        elif isinstance(message, TelemetrySubscribe):
            self._update_subscription(client, "telemetry", message.subscribe)

    @staticmethod
    def _required_permission(message: BaseMessage) -> str | None:
        if isinstance(message, ChatMessage):
            return "chat"
        if isinstance(message, StateSubscribe):
            return "state"
        if isinstance(message, TelemetrySubscribe):
            return "telemetry"
        if isinstance(message, ConfirmationRespond):
            return "confirmation"
        return None

    def _handle_pair(self, client: ClientConnection, message: AuthPair) -> None:
        device_name = self.pairing_manager.consume_pairing(
            message.pairing_code,
            message.device_name,
        )
        if device_name is None:
            self._send(
                client,
                AuthFailed(
                    code="pairing_failed",
                    message="Código inválido, expirado ou já utilizado.",
                    request_id=message.request_id,
                ),
            )
            return
        device_id = f"dev_{secrets.token_urlsafe(16)}"
        token = generate_token()
        now = datetime.now(timezone.utc).isoformat()
        device = Device(
            id=device_id,
            name=device_name,
            created_at=now,
            last_seen=now,
            token_hash=hash_token(token),
        )
        self.device_registry.add(device)
        self._authenticate_client(client, device)
        self._send(
            client,
            DevicePaired(
                device_id=device_id,
                device_name=device_name,
                token=token,
                request_id=message.request_id,
            ),
        )

    def _handle_token_auth(
        self,
        client: ClientConnection,
        message: AuthToken,
    ) -> None:
        try:
            self.device_registry.refresh()
            device = self.token_auth.authenticate(message.device_id, message.token)
        except Exception:
            device = None
        if device is None:
            self._audit_event("auth", message.device_id, False, "failed")
            self._send(
                client,
                AuthFailed(
                    code="auth_failed",
                    message="Token inválido ou dispositivo revogado.",
                    request_id=message.request_id,
                ),
            )
            return
        self._authenticate_client(client, device)
        self._send(
            client,
            AuthSuccess(device_id=device.id, request_id=message.request_id),
        )
        self._audit_event("auth", device.id, True, "success")

    def _authenticate_client(
        self,
        client: ClientConnection,
        device: Device,
    ) -> None:
        session = self.session_manager.create_session(device.id)
        client.session_id = session.id
        client.device_id = device.id
        client.authenticated = True
        self.bridge.register_session_sender(session.id, client.transport.send)

    def _update_subscription(
        self,
        client: ClientConnection,
        name: str,
        enabled: bool,
    ) -> None:
        if enabled:
            self.session_manager.add_subscription(client.session_id, name)
        else:
            self.session_manager.remove_subscription(client.session_id, name)

    def _send(self, client: ClientConnection, message: BaseMessage) -> bool:
        return client.transport.send(message)

    def _send_error(
        self,
        client: ClientConnection,
        code: str,
        message: str,
        request_id: str | None = None,
    ) -> None:
        self._send(
            client,
            Error(code=code, message=message, request_id=request_id),
        )

    def _disconnect_client(self, client: ClientConnection) -> None:
        with self._lock:
            registered = self._clients.pop(client.transport, None)
        if registered is None:
            client.transport.close()
            return
        if client.session_id:
            self.bridge.on_session_disconnect(client.session_id)
            self.session_manager.remove_session(client.session_id)
        client.transport.close()
        if client.device_id:
            self._audit_event("disconnect", client.device_id, True, "closed")

    def _cleanup_loop(self) -> None:
        while not self._stop_event.wait(1.0):
            try:
                self.pairing_manager.cleanup_expired()
                self.session_manager.cleanup_expired()
                self.device_registry.refresh()
            except Exception:
                self._audit_event("cleanup", "runtime", False, "error")
                continue
            with self._lock:
                clients = tuple(self._clients.values())
            for client in clients:
                if client.authenticated and not self._session_is_valid(client):
                    self._disconnect_client(client)

    def revoke_device(self, device_id: str) -> bool:
        if not self.device_registry.revoke(device_id):
            return False
        with self._lock:
            clients = tuple(
                client
                for client in self._clients.values()
                if client.device_id == device_id
            )
        for client in clients:
            self._send(client, DeviceRevoked(device_id=device_id))
            self._disconnect_client(client)
        return True

    def _audit_event(
        self,
        event: str,
        target: str,
        authorized: bool,
        status: str,
    ) -> None:
        if self.audit is not None:
            self.audit.log(
                "remote_gateway",
                f"{event}:{target}",
                authorized,
                status,
            )

    def __enter__(self) -> RemoteGateway:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()
