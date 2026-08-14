from typing import Any

from ollama import Client

from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest


class OllamaLLM(LLMClient):
    def __init__(self, model: str = "qwen3:4b") -> None:
        self.model = model
        self.client = Client(host="http://localhost:11434")

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:

        response = self.client.chat(
            model=self.model,
            messages=messages,
            tools=tools or [],
        )

        tool_calls = []

        for call in response.message.tool_calls or []:
            tool_calls.append(
                ToolCallRequest(
                    name=call.function.name,
                    arguments=dict(call.function.arguments),
                )
            )

        return LLMResponse(
            content=response.message.content or "",
            tool_calls=tool_calls,
        )