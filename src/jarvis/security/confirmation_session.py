"""Sessão thread-safe para confirmações críticas diferidas."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from queue import Empty, SimpleQueue
from threading import Condition, RLock, Thread
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4

from jarvis.security.risk import RiskLevel


class ConfirmationSessionState(Enum):
    REQUEST_CONFIRMATION = "request_confirmation"
    WAITING_CONFIRMATION = "waiting_confirmation"
    CONFIRMED = "confirmed"
    DENIED = "denied"
    EXPIRED = "expired"


class ConfirmationEventKind(Enum):
    REMINDER = "reminder"
    EXPIRED = "expired"


class ConfirmationDecision(Enum):
    CONFIRMED = "confirmed"
    DENIED = "denied"
    UNKNOWN = "unknown"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class PreparedInvocation:
    tool_name: str
    target: str
    arguments: Mapping[str, Any]
    arguments_digest: str
    risk: RiskLevel
    interaction_id: str


@dataclass(frozen=True, slots=True)
class ConfirmationSession:
    id: str
    challenge_id: str
    state: ConfirmationSessionState
    invocation: PreparedInvocation
    prompt_text: str
    created_at: float
    expires_at: float
    reminder_count: int = 0


@dataclass(frozen=True, slots=True)
class ConfirmationTimerEvent:
    kind: ConfirmationEventKind
    session_id: str
    reminder_index: int = 0


GrantFactory = Callable[[ConfirmationSession], Any | None]
CleanupCallback = Callable[[ConfirmationSession], None]


def digest_arguments(arguments: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ConfirmationSessionManager:
    """Mantém uma sessão ativa e agenda eventos sem bloquear o runtime."""

    def __init__(
        self,
        *,
        first_reminder_seconds: float = 25.0,
        reminder_interval_seconds: float = 30.0,
        max_reminders: int = 2,
        timeout_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
        cleanup: CleanupCallback | None = None,
        start_scheduler: bool = True,
    ) -> None:
        if first_reminder_seconds <= 0:
            raise ValueError("first_reminder_seconds deve ser positivo")
        if reminder_interval_seconds <= 0:
            raise ValueError("reminder_interval_seconds deve ser positivo")
        if max_reminders < 0 or max_reminders > 2:
            raise ValueError("max_reminders deve estar entre 0 e 2")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds deve ser positivo")
        self.first_reminder_seconds = first_reminder_seconds
        self.reminder_interval_seconds = reminder_interval_seconds
        self.max_reminders = max_reminders
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.cleanup = cleanup or (lambda _session: None)
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._active: ConfirmationSession | None = None
        self._sessions: dict[str, ConfirmationSession] = {}
        self._events: SimpleQueue[ConfirmationTimerEvent] = SimpleQueue()
        self._closed = False
        self._thread: Thread | None = None
        self._scheduler_enabled = start_scheduler

    def request(
        self,
        *,
        tool_name: str,
        target: str,
        arguments: Mapping[str, Any],
        risk: RiskLevel,
        interaction_id: str | None,
        prompt_text: str,
    ) -> ConfirmationSession | None:
        snapshot = json.loads(json.dumps(arguments, ensure_ascii=False))
        now = self.clock()
        with self._condition:
            self._expire_if_due_locked(now)
            if self._closed or self._active is not None:
                return None
            invocation = PreparedInvocation(
                tool_name=tool_name,
                target=target,
                arguments=MappingProxyType(snapshot),
                arguments_digest=digest_arguments(snapshot),
                risk=risk,
                interaction_id=interaction_id or str(uuid4()),
            )
            requested = ConfirmationSession(
                id=str(uuid4()),
                challenge_id=str(uuid4()),
                state=ConfirmationSessionState.REQUEST_CONFIRMATION,
                invocation=invocation,
                prompt_text=prompt_text,
                created_at=now,
                expires_at=now + self.timeout_seconds,
            )
            waiting = replace(
                requested,
                state=ConfirmationSessionState.WAITING_CONFIRMATION,
            )
            self._active = waiting
            self._sessions[waiting.id] = waiting
            self._ensure_scheduler_locked()
            self._condition.notify_all()
            return waiting

    def current(self) -> ConfirmationSession | None:
        with self._condition:
            self._expire_if_due_locked(self.clock())
            return self._active

    def context(self) -> tuple[str, str] | None:
        session = self.current()
        if session is None:
            return None
        return session.id, session.challenge_id

    def state(self) -> ConfirmationSessionState | None:
        with self._lock:
            if self._active is not None:
                return self._active.state
            if not self._sessions:
                return None
            last_id = next(reversed(self._sessions))
            return self._sessions[last_id].state

    def decide(
        self,
        *,
        confirmed: bool,
        denied: bool,
        session_id: str | None,
        challenge_id: str | None,
        grant_factory: GrantFactory,
    ) -> tuple[ConfirmationDecision, ConfirmationSession | None, Any | None]:
        with self._condition:
            self._expire_if_due_locked(self.clock())
            session = self._active
            if session is None:
                return ConfirmationDecision.STALE, None, None
            if (
                session_id != session.id
                or challenge_id != session.challenge_id
            ):
                return ConfirmationDecision.STALE, session, None
            if denied:
                terminal = self._finish_locked(
                    session, ConfirmationSessionState.DENIED
                )
                return ConfirmationDecision.DENIED, terminal, None
            if not confirmed:
                return ConfirmationDecision.UNKNOWN, session, None
            grant = grant_factory(session)
            if grant is None:
                terminal = self._finish_locked(
                    session, ConfirmationSessionState.DENIED
                )
                return ConfirmationDecision.DENIED, terminal, None
            terminal = self._finish_locked(
                session, ConfirmationSessionState.CONFIRMED, cleanup=False
            )
            return ConfirmationDecision.CONFIRMED, terminal, grant

    def cancel(self) -> bool:
        with self._condition:
            session = self._active
            if session is None:
                return False
            self._finish_locked(session, ConfirmationSessionState.DENIED)
            return True

    def process_due(self, now: float | None = None) -> None:
        with self._condition:
            current = self.clock() if now is None else now
            self._expire_if_due_locked(current)
            session = self._active
            if session is None or self.max_reminders == 0:
                return
            while session.reminder_count < self.max_reminders:
                deadline = (
                    session.created_at
                    + self.first_reminder_seconds
                    + session.reminder_count * self.reminder_interval_seconds
                )
                if current < deadline or deadline >= session.expires_at:
                    break
                session = replace(
                    session,
                    reminder_count=session.reminder_count + 1,
                )
                self._active = session
                self._sessions[session.id] = session
                self._events.put(
                    ConfirmationTimerEvent(
                        ConfirmationEventKind.REMINDER,
                        session.id,
                        session.reminder_count,
                    )
                )

    def pop_event(self) -> ConfirmationTimerEvent | None:
        try:
            return self._events.get_nowait()
        except Empty:
            return None

    def invalidate_session(self, session_id: str) -> None:
        with self._condition:
            session = self._active
            if session is not None and session.id == session_id:
                self._finish_locked(session, ConfirmationSessionState.DENIED)

    def close(self) -> None:
        thread = None
        with self._condition:
            if self._closed:
                return
            self._closed = True
            if self._active is not None:
                self._finish_locked(
                    self._active, ConfirmationSessionState.DENIED
                )
            thread = self._thread
            self._condition.notify_all()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _finish_locked(
        self,
        session: ConfirmationSession,
        state: ConfirmationSessionState,
        *,
        cleanup: bool = True,
    ) -> ConfirmationSession:
        terminal = replace(session, state=state)
        self._sessions[terminal.id] = terminal
        if self._active is session or (
            self._active is not None and self._active.id == session.id
        ):
            self._active = None
        if cleanup:
            self.cleanup(terminal)
        self._condition.notify_all()
        return terminal

    def _expire_if_due_locked(self, now: float) -> None:
        session = self._active
        if session is None or now < session.expires_at:
            return
        terminal = self._finish_locked(
            session, ConfirmationSessionState.EXPIRED
        )
        self._events.put(
            ConfirmationTimerEvent(
                ConfirmationEventKind.EXPIRED,
                terminal.id,
            )
        )

    def _next_deadline_locked(self) -> float | None:
        session = self._active
        if session is None:
            return None
        deadlines = [session.expires_at]
        if session.reminder_count < self.max_reminders:
            deadlines.append(
                session.created_at
                + self.first_reminder_seconds
                + session.reminder_count * self.reminder_interval_seconds
            )
        return min(deadlines)

    def _scheduler_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                deadline = self._next_deadline_locked()
                if deadline is None:
                    self._condition.wait()
                    continue
                delay = max(0.0, deadline - self.clock())
                if delay > 0:
                    self._condition.wait(timeout=delay)
                    continue
            self.process_due()

    def _ensure_scheduler_locked(self) -> None:
        if (
            not self._scheduler_enabled
            or self._closed
            or (self._thread is not None and self._thread.is_alive())
        ):
            return
        self._thread = Thread(
            target=self._scheduler_loop,
            name="jarvis-confirmation-scheduler",
            daemon=True,
        )
        self._thread.start()
