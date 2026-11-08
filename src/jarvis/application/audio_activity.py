"""Observabilidade leve do nível de áudio para a interface.

O barramento publica apenas escalares normalizados; áudio bruto nunca atravessa
esta camada. Isso mantém a Ambient UI reativa sem ampliar autoridade ou expor
conteúdo de voz.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class AudioActivitySnapshot:
    level: float = 0.0
    peak: float = 0.0
    sequence: int = 0


AudioActivitySubscriber = Callable[[AudioActivitySnapshot], None]


class AudioActivityBus:
    """Barramento thread-safe de atividade de áudio normalizada (0..1)."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshot = AudioActivitySnapshot()
        self._subscribers: list[AudioActivitySubscriber] = []

    @property
    def snapshot(self) -> AudioActivitySnapshot:
        with self._lock:
            return self._snapshot

    def publish(self, level: float, *, peak: float | None = None) -> None:
        normalized_level = self._normalize(level)
        normalized_peak = self._normalize(
            normalized_level if peak is None else peak
        )
        with self._lock:
            snapshot = AudioActivitySnapshot(
                level=normalized_level,
                peak=max(normalized_level, normalized_peak),
                sequence=self._snapshot.sequence + 1,
            )
            self._snapshot = snapshot
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(snapshot)
            except Exception:
                continue

    def reset(self) -> None:
        self.publish(0.0, peak=0.0)

    def subscribe(
        self,
        subscriber: AudioActivitySubscriber,
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

    @staticmethod
    def _normalize(value: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))
