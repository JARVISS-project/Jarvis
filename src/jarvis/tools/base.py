from abc import ABC, abstractmethod
from typing import Any

from jarvis.security.risk import RiskLevel


class Tool(ABC):
    name: str
    description: str
    risk: RiskLevel
    parameters: dict[str, Any]

    @abstractmethod
    def get_target(self, **kwargs: Any) -> str:
        raise NotImplementedError

    def preflight(self, **kwargs: Any) -> None:
        """Validate an operation without changing external state."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError
