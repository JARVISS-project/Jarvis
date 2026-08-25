from abc import ABC, abstractmethod

from jarvis.memory.models import MemoryEntry


class MemoryStore(ABC):
    @abstractmethod
    def add(self, content: str, category: str) -> MemoryEntry:
        pass

    @abstractmethod
    def get_all(self) -> list[MemoryEntry]:
        pass

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass
