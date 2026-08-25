from datetime import datetime, timezone
from uuid import uuid4

from jarvis.memory.models import MemoryEntry
from jarvis.memory.store import MemoryStore


class InMemoryMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    def add(self, content: str, category: str) -> MemoryEntry:
        entry = MemoryEntry(
            id=str(uuid4()),
            content=content,
            category=category,
            created_at=datetime.now(timezone.utc),
        )
        self._entries.append(entry)
        return entry

    def get_all(self) -> list[MemoryEntry]:
        return self._entries.copy()

    def delete(self, memory_id: str) -> bool:
        for index, entry in enumerate(self._entries):
            if entry.id == memory_id:
                del self._entries[index]
                return True

        return False

    def clear(self) -> None:
        self._entries.clear()
