from abc import ABC, abstractmethod
from typing import Any

from jarvis.memory.models import MemoryEntry


class MemoryStore(ABC):
    def update(self, memory_id: str, *, content: str, category: str,
               retention_days: int | None = None, consent_given: bool = True,
               metadata: dict[str, Any] | None = None) -> bool:
        raise NotImplementedError("Este armazenamento não oferece edição.")

    @abstractmethod
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
        pass

    @abstractmethod
    def get_all(self) -> list[MemoryEntry]:
        pass

    @abstractmethod
    def get_relevant(
        self,
        query: str,
        max_results: int = 5,
        min_score: float = 0.3,
    ) -> list[MemoryEntry]:
        pass

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        pass

    @abstractmethod
    def update_consent(self, memory_id: str, consent: bool) -> bool:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass

    @abstractmethod
    def cleanup_expired(self) -> int:
        pass
