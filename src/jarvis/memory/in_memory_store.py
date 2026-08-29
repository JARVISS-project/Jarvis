from datetime import datetime, timezone
from dataclasses import replace
from typing import Any
from uuid import uuid4

from jarvis.memory.models import (
    MemoryEntry,
    MemoryProvenance,
    MemorySensitivity,
    memory_relevance,
    memory_tokens,
)
from jarvis.memory.store import MemoryStore


class InMemoryMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    def add(
        self,
        content: str,
        category: str,
        *,
        sensitivity: str = "private",
        provenance: str = "user_explicit",
        retention_days: int | None = None,
        consent_given: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            id=uuid4().hex[:12],
            content=content,
            category=category,
            created_at=datetime.now(timezone.utc),
            sensitivity=MemorySensitivity(sensitivity),
            provenance=MemoryProvenance(provenance),
            retention_days=retention_days,
            consent_given=consent_given,
            metadata=metadata or {},
        )
        self._entries.append(entry)
        return entry

    def get_all(self) -> list[MemoryEntry]:
        self.cleanup_expired()
        return self._entries.copy()

    def get_relevant(
        self,
        query: str,
        max_results: int = 5,
        min_score: float = 0.3,
    ) -> list[MemoryEntry]:
        self.cleanup_expired()
        if not query.strip():
            return []

        query_terms = memory_tokens(query)
        if not query_terms:
            return []

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._entries:
            if not entry.consent_given or entry.is_expired():
                continue
            score = memory_relevance(entry, query_terms)
            if score >= min_score:
                scored.append((score, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored[:max_results]]

    def delete(self, memory_id: str) -> bool:
        for index, entry in enumerate(self._entries):
            if entry.id == memory_id:
                del self._entries[index]
                return True

        return False

    def clear(self) -> None:
        self._entries.clear()

    def cleanup_expired(self) -> int:
        before = len(self._entries)
        self._entries = [entry for entry in self._entries if not entry.is_expired()]
        return before - len(self._entries)

    def update_consent(self, memory_id: str, consent: bool) -> bool:
        for index, entry in enumerate(self._entries):
            if entry.id == memory_id:
                self._entries[index] = replace(entry, consent_given=consent)
                return True
        return False
