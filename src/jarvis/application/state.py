"""Estado observável do Jarvis sem dependência de interface gráfica."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from threading import RLock


class AgentState(Enum):
    """Estados que consumidores como UI, telemetria ou API podem observar."""

    WAITING_WAKE = "waiting_wake"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    WAITING_FOLLOWUP = "waiting_followup"
    WAITING_REPAIR = "waiting_repair"
    CONFIRMATION = "confirmation"
    EXECUTING = "executing"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """Registro imutável de uma transição de estado do agente."""

    state: AgentState
    previous_state: AgentState | None
    sequence: int
    source: str
    detail: str | None = None


StateSubscriber = Callable[[StateSnapshot], None]


class AgentStateBus:
    """Barramento pequeno e thread-safe para publicar estado do agente.

    Falhas de consumidores são isoladas de propósito: uma UI quebrada não pode
    derrubar o pipeline de voz ou outra parte do Core.
    """

    def __init__(self, initial_state: AgentState = AgentState.WAITING_WAKE) -> None:
        self._lock = RLock()
        self._subscribers: list[StateSubscriber] = []
        self._snapshot = StateSnapshot(
            state=initial_state,
            previous_state=None,
            sequence=0,
            source="startup",
        )

    @property
    def snapshot(self) -> StateSnapshot:
        with self._lock:
            return self._snapshot

    def publish(
        self,
        state: AgentState,
        *,
        source: str,
        detail: str | None = None,
    ) -> StateSnapshot:
        with self._lock:
            previous = self._snapshot.state
            snapshot = StateSnapshot(
                state=state,
                previous_state=previous,
                sequence=self._snapshot.sequence + 1,
                source=source,
                detail=detail,
            )
            self._snapshot = snapshot
            subscribers = tuple(self._subscribers)

        for subscriber in subscribers:
            try:
                subscriber(snapshot)
            except Exception:
                # Observadores nunca devem comprometer o runtime principal.
                continue
        return snapshot

    def subscribe(
        self,
        subscriber: StateSubscriber,
        *,
        replay_current: bool = True,
    ) -> Callable[[], None]:
        with self._lock:
            if subscriber not in self._subscribers:
                self._subscribers.append(subscriber)
            snapshot = self._snapshot

        if replay_current:
            try:
                subscriber(snapshot)
            except Exception:
                pass

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe
