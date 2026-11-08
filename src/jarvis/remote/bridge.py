"""Bridge sem autoridade entre sessões remotas e o Core."""

from __future__ import annotations

from dataclasses import dataclass, replace
import threading
from typing import Callable

from jarvis.application.state import AgentStateBus, StateSnapshot
from jarvis.application.telemetry import TelemetryBus, TelemetrySnapshot
from jarvis.remote.core_port import (
    RemoteConfirmation,
    RemoteCorePort,
    RemoteSecuritySnapshot,
)
from jarvis.remote.protocol import (
    AgentState,
    BaseMessage,
    ChatMessage,
    ChatResponse,
    ConfirmationRequest,
    ConfirmationRespond,
    Error,
    SecuritySubscribe,
    SecurityUpdate,
    StateSubscribe,
    StateUpdate,
    TelemetrySubscribe,
    TelemetryUpdate,
)
from jarvis.security.audit import AuditLogger


@dataclass(frozen=True, slots=True)
class PendingRemoteConfirmation:
    remote_session_id: str
    confirmation_session_id: str
    challenge_id: str
    reminder_count: int


MessageSender = Callable[[BaseMessage], bool]


class RemoteGatewayBridge:
    """Encaminha mensagens somente pela ``RemoteCorePort`` autorizada."""

    def __init__(
        self,
        core_port: RemoteCorePort,
        state_bus: AgentStateBus,
        telemetry_bus: TelemetryBus,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._core_port = core_port
        self._state_bus = state_bus
        self._telemetry_bus = telemetry_bus
        self._audit = audit_logger
        self._lock = threading.RLock()
        self._pending_confirmations: dict[str, PendingRemoteConfirmation] = {}
        self._state_subscribers: set[str] = set()
        self._telemetry_subscribers: set[str] = set()
        self._security_subscribers: set[str] = set()
        self._session_senders: dict[str, MessageSender] = {}
        self._security_snapshot: RemoteSecuritySnapshot | None = None
        self._security_sequence = 0
        self._unsubscribe_state: Callable[[], None] | None = None
        self._unsubscribe_telemetry: Callable[[], None] | None = None
        self._stop_event = threading.Event()
        self._confirmation_thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._confirmation_thread is not None:
                return
            self._unsubscribe_state = self._state_bus.subscribe(self._on_state_change)
            self._unsubscribe_telemetry = self._telemetry_bus.subscribe(
                self._on_telemetry_change
            )
            self._stop_event.clear()
            self._confirmation_thread = threading.Thread(
                target=self._confirmation_watch_loop,
                name="jarvis-remote-confirmation-watch",
                daemon=True,
            )
            self._confirmation_thread.start()

    def register_session_sender(
        self,
        session_id: str,
        sender: MessageSender,
    ) -> None:
        with self._lock:
            self._session_senders[session_id] = sender

    def handle_message(self, session_id: str, message: BaseMessage) -> None:
        try:
            if isinstance(message, ChatMessage):
                self._handle_chat(session_id, message)
            elif isinstance(message, StateSubscribe):
                self._handle_state_subscribe(session_id, message)
            elif isinstance(message, TelemetrySubscribe):
                self._handle_telemetry_subscribe(session_id, message)
            elif isinstance(message, SecuritySubscribe):
                self._handle_security_subscribe(session_id, message)
            elif isinstance(message, ConfirmationRespond):
                self._handle_confirmation_response(session_id, message)
            else:
                self._send(
                    session_id,
                    Error(
                        code="unsupported_message",
                        message="Tipo de mensagem não suportado.",
                        request_id=message.request_id,
                    ),
                )
        except Exception:
            self._audit_event("bridge_error", session_id, False, "error")
            self._send(
                session_id,
                Error(
                    code="internal_error",
                    message="Falha interna ao processar a solicitação.",
                    request_id=message.request_id,
                ),
            )

    def _handle_chat(self, session_id: str, message: ChatMessage) -> None:
        before = self._core_port.current_confirmation()
        try:
            response = self._core_port.process_chat(message.text)
        except Exception:
            self._audit_event("chat", session_id, False, "error")
            self._send(
                session_id,
                Error(
                    code="chat_failed",
                    message="O Core não conseguiu processar a mensagem.",
                    request_id=message.request_id,
                ),
            )
            return
        self._audit_event("chat", session_id, True, "processed")
        self._send(
            session_id,
            ChatResponse(text=response, request_id=message.request_id),
        )
        after = self._core_port.current_confirmation()
        if before is None and after is not None:
            pending = PendingRemoteConfirmation(
                remote_session_id=session_id,
                confirmation_session_id=after.session_id,
                challenge_id=after.challenge_id,
                reminder_count=after.reminder_count,
            )
            with self._lock:
                self._pending_confirmations[after.session_id] = pending
            self._send_confirmation(session_id, after, message.request_id)

    def _handle_confirmation_response(
        self,
        remote_session_id: str,
        message: ConfirmationRespond,
    ) -> None:
        with self._lock:
            pending = self._pending_confirmations.get(message.session_id)
        if pending is None or pending.remote_session_id != remote_session_id:
            self._send(
                remote_session_id,
                Error(
                    code="invalid_confirmation",
                    message="Confirmação inválida ou expirada.",
                    request_id=message.request_id,
                ),
            )
            return
        if pending.challenge_id != message.challenge_id:
            self._send(
                remote_session_id,
                Error(
                    code="challenge_mismatch",
                    message="A confirmação não corresponde ao desafio ativo.",
                    request_id=message.request_id,
                ),
            )
            return
        response = self._core_port.process_confirmation(
            confirmed=message.confirmed,
            session_id=message.session_id,
            challenge_id=message.challenge_id,
        )
        with self._lock:
            self._pending_confirmations.pop(message.session_id, None)
        self._audit_event(
            "confirmation",
            remote_session_id,
            message.confirmed,
            "confirmed" if message.confirmed else "denied",
        )
        self._send(
            remote_session_id,
            ChatResponse(text=response, request_id=message.request_id),
        )

    def _handle_state_subscribe(
        self,
        session_id: str,
        message: StateSubscribe,
    ) -> None:
        with self._lock:
            if message.subscribe:
                self._state_subscribers.add(session_id)
            else:
                self._state_subscribers.discard(session_id)
        if message.subscribe:
            self._send_state(session_id, self._state_bus.snapshot, message.request_id)

    def _handle_telemetry_subscribe(
        self,
        session_id: str,
        message: TelemetrySubscribe,
    ) -> None:
        with self._lock:
            if message.subscribe:
                self._telemetry_subscribers.add(session_id)
            else:
                self._telemetry_subscribers.discard(session_id)
        if message.subscribe:
            self._send_telemetry(
                session_id,
                self._telemetry_bus.snapshot,
                message.request_id,
            )

    def _handle_security_subscribe(
        self,
        session_id: str,
        message: SecuritySubscribe,
    ) -> None:
        with self._lock:
            if message.subscribe:
                self._security_subscribers.add(session_id)
            else:
                self._security_subscribers.discard(session_id)
        if not message.subscribe:
            return
        snapshot = self._core_port.current_security()
        sequence, _changed = self._record_security_snapshot(snapshot)
        self._send_security(session_id, snapshot, sequence, message.request_id)

    def _record_security_snapshot(
        self,
        snapshot: RemoteSecuritySnapshot,
    ) -> tuple[int, bool]:
        with self._lock:
            changed = snapshot != self._security_snapshot
            if changed:
                self._security_snapshot = snapshot
                self._security_sequence += 1
            return self._security_sequence, changed

    def _send_security(
        self,
        session_id: str,
        snapshot: RemoteSecuritySnapshot,
        sequence: int,
        request_id: str | None = None,
    ) -> None:
        self._send(
            session_id,
            SecurityUpdate(
                sequence=sequence,
                kill_switch_tripped=snapshot.kill_switch_tripped,
                capabilities=dict(snapshot.capabilities),
                request_id=request_id,
            ),
        )

    def _on_state_change(self, snapshot: StateSnapshot) -> None:
        with self._lock:
            subscribers = tuple(self._state_subscribers)
        for session_id in subscribers:
            self._send_state(session_id, snapshot)

    def _send_state(
        self,
        session_id: str,
        snapshot: StateSnapshot,
        request_id: str | None = None,
    ) -> None:
        self._send(
            session_id,
            StateUpdate(
                state=AgentState(snapshot.state.value),
                sequence=snapshot.sequence,
                source=snapshot.source,
                request_id=request_id,
            ),
        )

    def _on_telemetry_change(self, snapshot: TelemetrySnapshot) -> None:
        with self._lock:
            subscribers = tuple(self._telemetry_subscribers)
        for session_id in subscribers:
            self._send_telemetry(session_id, snapshot)

    def _send_telemetry(
        self,
        session_id: str,
        snapshot: TelemetrySnapshot,
        request_id: str | None = None,
    ) -> None:
        self._send(
            session_id,
            TelemetryUpdate(
                sequence=snapshot.sequence,
                has_completed_turn=snapshot.has_completed_turn,
                turn_ms=snapshot.turn_ms,
                llm_ms=snapshot.llm_ms,
                llm_calls=snapshot.llm_calls,
                tool_ms=snapshot.tool_ms,
                tool_calls=snapshot.tool_calls,
                audit_events=snapshot.audit_events,
                audit_write_ok=snapshot.audit_write_ok,
                request_id=request_id,
            ),
        )

    def _confirmation_watch_loop(self) -> None:
        while not self._stop_event.wait(0.1):
            try:
                current = self._core_port.current_confirmation()
            except Exception:
                self._audit_event("confirmation_watch", "core", False, "error")
                continue
            with self._lock:
                pending_items = tuple(self._pending_confirmations.items())
            for confirmation_id, pending in pending_items:
                if current is None or current.session_id != confirmation_id:
                    with self._lock:
                        removed = self._pending_confirmations.pop(
                            confirmation_id,
                            None,
                        )
                    if removed is not None:
                        self._send(
                            pending.remote_session_id,
                            Error(
                                code="confirmation_expired",
                                message="A confirmação expirou ou foi encerrada.",
                            ),
                        )
                    continue
                if current.reminder_count > pending.reminder_count:
                    with self._lock:
                        self._pending_confirmations[confirmation_id] = replace(
                            pending,
                            reminder_count=current.reminder_count,
                        )
                    self._send_confirmation(pending.remote_session_id, current)
            with self._lock:
                security_subscribers = tuple(self._security_subscribers)
            if security_subscribers:
                try:
                    security = self._core_port.current_security()
                except Exception:
                    self._audit_event("security_watch", "core", False, "error")
                    continue
                sequence, changed = self._record_security_snapshot(security)
                if changed:
                    for session_id in security_subscribers:
                        self._send_security(session_id, security, sequence)

    def _send_confirmation(
        self,
        session_id: str,
        confirmation: RemoteConfirmation,
        request_id: str | None = None,
    ) -> None:
        self._send(
            session_id,
            ConfirmationRequest(
                session_id=confirmation.session_id,
                challenge_id=confirmation.challenge_id,
                action=confirmation.action,
                target=confirmation.target,
                risk=confirmation.risk,
                prompt_text=confirmation.prompt_text,
                expires_at=confirmation.expires_at,
                request_id=request_id,
            ),
        )

    def _send(self, session_id: str, message: BaseMessage) -> bool:
        with self._lock:
            sender = self._session_senders.get(session_id)
        return sender(message) if sender is not None else False

    def on_session_disconnect(self, session_id: str) -> None:
        with self._lock:
            self._state_subscribers.discard(session_id)
            self._telemetry_subscribers.discard(session_id)
            self._security_subscribers.discard(session_id)
            self._session_senders.pop(session_id, None)
            pending = [
                value
                for value in self._pending_confirmations.values()
                if value.remote_session_id == session_id
            ]
            for value in pending:
                self._pending_confirmations.pop(
                    value.confirmation_session_id,
                    None,
                )
        for value in pending:
            try:
                self._core_port.process_confirmation(
                    confirmed=False,
                    session_id=value.confirmation_session_id,
                    challenge_id=value.challenge_id,
                )
            except Exception:
                self._audit_event("confirmation_disconnect", session_id, False, "error")

    def stop(self) -> None:
        with self._lock:
            thread = self._confirmation_thread
            if thread is None:
                return
            self._confirmation_thread = None
            self._stop_event.set()
            unsubscribe_state = self._unsubscribe_state
            unsubscribe_telemetry = self._unsubscribe_telemetry
            self._unsubscribe_state = None
            self._unsubscribe_telemetry = None
        if thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if unsubscribe_state is not None:
            unsubscribe_state()
        if unsubscribe_telemetry is not None:
            unsubscribe_telemetry()
        with self._lock:
            self._pending_confirmations.clear()
            self._state_subscribers.clear()
            self._telemetry_subscribers.clear()
            self._security_subscribers.clear()
            self._session_senders.clear()
            self._security_snapshot = None
            self._security_sequence = 0

    close = stop

    def _audit_event(
        self,
        event: str,
        target: str,
        authorized: bool,
        status: str,
    ) -> None:
        if self._audit is not None:
            self._audit.log(
                "remote_gateway",
                f"{event}:{target}",
                authorized,
                status,
            )
