from abc import ABC, abstractmethod
from typing import Any

from jarvis.llm.types import LLMResponse


class LLMClient(ABC):
    def set_keep_alive(self, keep_alive: str) -> None:
        pass

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        raise NotImplementedError
