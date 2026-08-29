"""Telemetria agregada e não sensível para consumidores visuais locais."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import RLock


MAX_DURATION_MS = 3_600_000
MAX_COUNT = 1_000_000_000


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    sequence: int = 0
    has_completed_turn: bool = False
    turn_ms: int = 0
    llm_ms: int = 0
    llm_calls: int = 0
    tool_ms: int = 0
    tool_calls: int = 0
    audit_events: int = 0
    audit_write_ok: bool | None = None


TelemetrySubscriber = Callable[[TelemetrySnapshot], None]


class TelemetryBus:
    """Mantém somente o agregado mais recente, sem contexto operacional."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshot = TelemetrySnapshot()
        self._subscribers: list[TelemetrySubscriber] = []

    @property
    def snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            return self._snapshot

    def publish_turn(
        self,
        *,
        turn_ms: int,
        llm_ms: int,
        llm_calls: int,
        tool_ms: int,
        tool_calls: int,
    ) -> TelemetrySnapshot:
        values = {
            "turn_ms": self._bounded(turn_ms, MAX_DURATION_MS),
            "llm_ms": self._bounded(llm_ms, MAX_DURATION_MS),
            "llm_calls": self._bounded(llm_calls, MAX_COUNT),
            "tool_ms": self._bounded(tool_ms, MAX_DURATION_MS),
            "tool_calls": self._bounded(tool_calls, MAX_COUNT),
        }
        with self._lock:
            snapshot = replace(
                self._snapshot,
                sequence=min(self._snapshot.sequence + 1, MAX_COUNT),
                has_completed_turn=True,
                **values,
            )
            self._snapshot = snapshot
            subscribers = tuple(self._subscribers)
        self._notify_if_current(subscribers, snapshot)
        return snapshot

    def record_audit(self, succeeded: bool) -> TelemetrySnapshot:
        with self._lock:
            snapshot = replace(
                self._snapshot,
                sequence=min(self._snapshot.sequence + 1, MAX_COUNT),
                audit_events=(
                    min(self._snapshot.audit_events + 1, MAX_COUNT)
                    if succeeded
                    else self._snapshot.audit_events
                ),
                audit_write_ok=succeeded,
            )
            self._snapshot = snapshot
            subscribers = tuple(self._subscribers)
        self._notify_if_current(subscribers, snapshot)
        return snapshot

    def publish_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._validate_snapshot(snapshot)
        with self._lock:
            if snapshot.sequence < self._snapshot.sequence:
                return
            self._snapshot = snapshot
            subscribers = tuple(self._subscribers)
        self._notify(subscribers, snapshot)

    def subscribe(
        self,
        subscriber: TelemetrySubscriber,
        *,
        replay_current: bool = True,
    ) -> Callable[[], None]:
        with self._lock:
            if subscriber not in self._subscribers:
                self._subscribers.append(subscriber)
            snapshot = self._snapshot
        if replay_current:
            self._notify((subscriber,), snapshot)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    @staticmethod
    def _bounded(value: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("Métrica de telemetria deve ser inteira.")
        if value < 0:
            raise ValueError("Métrica de telemetria não pode ser negativa.")
        return min(value, maximum)

    @classmethod
    def _validate_snapshot(cls, snapshot: TelemetrySnapshot) -> None:
        if not isinstance(snapshot.has_completed_turn, bool):
            raise TypeError("Estado de turno inválido.")
        cls._bounded(snapshot.sequence, MAX_COUNT)
        cls._bounded(snapshot.turn_ms, MAX_DURATION_MS)
        cls._bounded(snapshot.llm_ms, MAX_DURATION_MS)
        cls._bounded(snapshot.llm_calls, MAX_COUNT)
        cls._bounded(snapshot.tool_ms, MAX_DURATION_MS)
        cls._bounded(snapshot.tool_calls, MAX_COUNT)
        cls._bounded(snapshot.audit_events, MAX_COUNT)
        if snapshot.audit_write_ok not in {True, False, None}:
            raise TypeError("Estado de audit inválido.")

    @staticmethod
    def _notify(
        subscribers: tuple[TelemetrySubscriber, ...],
        snapshot: TelemetrySnapshot,
    ) -> None:
        for subscriber in subscribers:
            try:
                subscriber(snapshot)
            except Exception:
                continue

    def _notify_if_current(
        self,
        subscribers: tuple[TelemetrySubscriber, ...],
        snapshot: TelemetrySnapshot,
    ) -> None:
        with self._lock:
            if snapshot.sequence < self._snapshot.sequence:
                return
        self._notify(subscribers, snapshot)
