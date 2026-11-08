"""Histórico curto e efêmero da conversa para a UI local.

Não persiste em disco. Mantém somente os últimos eventos em memória e existe
apenas para observabilidade da conversa na interface.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class ConversationEntry:
    role: str
    text: str
    sequence: int


@dataclass(frozen=True, slots=True)
class ConversationLogSnapshot:
    entries: tuple[ConversationEntry, ...] = ()
    sequence: int = 0


ConversationLogSubscriber = Callable[[ConversationLogSnapshot], None]


class ConversationLogBus:
    def __init__(self, *, max_entries: int = 12) -> None:
        self._lock = RLock()
        self._entries: deque[ConversationEntry] = deque(
            maxlen=max(2, int(max_entries))
        )
        self._sequence = 0
        self._subscribers: list[ConversationLogSubscriber] = []

    @property
    def snapshot(self) -> ConversationLogSnapshot:
        with self._lock:
            return ConversationLogSnapshot(
                entries=tuple(self._entries),
                sequence=self._sequence,
            )

    def add_user(self, text: str) -> None:
        self._append("user", text)

    def add_assistant(self, text: str) -> None:
        self._append("assistant", text)

    def _append(self, role: str, text: str) -> None:
        normalized = str(text).strip()
        if not normalized:
            return
        with self._lock:
            self._sequence += 1
            self._entries.append(
                ConversationEntry(
                    role=role,
                    text=normalized,
                    sequence=self._sequence,
                )
            )
            snapshot = ConversationLogSnapshot(
                entries=tuple(self._entries),
                sequence=self._sequence,
            )
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(snapshot)
            except Exception:
                continue

    def publish_snapshot(self, snapshot: ConversationLogSnapshot) -> None:
        with self._lock:
            if snapshot.sequence < self._sequence:
                return
            self._entries.clear()
            for entry in snapshot.entries:
                self._entries.append(entry)
            self._sequence = snapshot.sequence
            current = ConversationLogSnapshot(
                entries=tuple(self._entries),
                sequence=self._sequence,
            )
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(current)
            except Exception:
                continue

    def subscribe(
        self,
        subscriber: ConversationLogSubscriber,
        *,
        replay_current: bool = True,
    ) -> Callable[[], None]:
        with self._lock:
            if subscriber not in self._subscribers:
                self._subscribers.append(subscriber)
            snapshot = ConversationLogSnapshot(
                entries=tuple(self._entries),
                sequence=self._sequence,
            )
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
