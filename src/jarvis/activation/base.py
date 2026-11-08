from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ActivationEvent:
    source: str
    timestamp: datetime


class ActivationSource(ABC):
    @abstractmethod
    def get_event(self) -> ActivationEvent | None:
        raise NotImplementedError
