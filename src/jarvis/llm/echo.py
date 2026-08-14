from typing import Any

from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse


class EchoLLM(LLMClient):
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        last_message = messages[-1]["content"]

        return LLMResponse(
            content=f"Você disse: {last_message}"
        )