from abc import ABC, abstractmethod
from typing import Any

from jarvis.llm.types import LLMResponse


class LLMClient(ABC):
    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        raise NotImplementedError