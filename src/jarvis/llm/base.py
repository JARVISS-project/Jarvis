from abc import ABC, abstractmethod
from typing import Any

from jarvis.llm.types import LLMResponse


class LLMClient(ABC):
    def set_keep_alive(self, keep_alive: str) -> None:
        pass

    def warm_up(self) -> None:
        """Optionally load the model without affecting conversation state."""
        return None

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    def generate_final(
        self,
        messages: list[dict[str, Any]],
    ) -> LLMResponse:
        """Formula texto final sem disponibilizar Tools."""
        return self.generate(messages, tools=[])
