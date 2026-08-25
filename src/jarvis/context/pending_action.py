"""Contexto efêmero para uma Tool que aguarda dados do usuário."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import Lock
from typing import Any, Callable
from uuid import uuid4


class PendingActionState(Enum):
    WAITING_TARGET = "waiting_target"
    WAITING_TARGET_CONFIRMATION = "waiting_target_confirmation"
    WAITING_INTENT_CONFIRMATION = "waiting_intent_confirmation"
    WAITING_SECURITY_CONFIRMATION = "waiting_security_confirmation"
    READY_TO_EXECUTE = "ready_to_execute"
    CANCELED = "canceled"


@dataclass(frozen=True)
class PendingAction:
    tool_name: str
    original_request: str
    missing_field: str
    resolved_arguments: dict[str, Any] = field(default_factory=dict)
    candidate_targets: tuple[str, ...] = ()
    state: PendingActionState = PendingActionState.WAITING_TARGET
    interaction_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class PendingActionContext:
    """Mantém no máximo uma ação pendente, somente em RAM."""

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds deve ser positivo")
        self.timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._pending: PendingAction | None = None
        self._lock = Lock()

    def set(self, action: PendingAction) -> None:
        with self._lock:
            self._pending = action

    def get(self) -> PendingAction | None:
        with self._lock:
            pending = self._pending
            if pending is None:
                return None
            expires_at = pending.created_at + timedelta(
                seconds=self.timeout_seconds
            )
            if self._clock() >= expires_at:
                self._pending = None
                return None
            return pending

    def has_pending(self) -> bool:
        return self.get() is not None

    def clear(self) -> None:
        with self._lock:
            self._pending = None

    def transition(self, state: PendingActionState) -> PendingAction | None:
        with self._lock:
            if self._pending is None:
                return None
            self._pending = replace(self._pending, state=state)
            return self._pending

    def cancel(self) -> None:
        with self._lock:
            if self._pending is not None:
                self._pending = replace(
                    self._pending, state=PendingActionState.CANCELED
                )
            self._pending = None
