from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from jarvis.security.risk import RiskLevel


@dataclass(frozen=True, slots=True)
class ToolPreparation:
    target: str
    context: object | None = field(default=None, repr=False)
    binding_digest: str | None = field(default=None, repr=False)


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

    def prepare(self, **kwargs: Any) -> ToolPreparation:
        return ToolPreparation(self.get_target(**kwargs))

    def preflight_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> None:
        self.preflight(**kwargs)

    def refresh_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> ToolPreparation:
        return preparation

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError
