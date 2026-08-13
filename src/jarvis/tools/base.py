from abc import ABC, abstractmethod
from typing import Any

from jarvis.security.risk import RiskLevel


class Tool(ABC):
    name: str
    description: str
    risk: RiskLevel

    @abstractmethod
    def get_target(self, **kwargs: Any) -> str:
        raise NotImplementedError

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError