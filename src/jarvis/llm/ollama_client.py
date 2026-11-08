from typing import Any
import json

from ollama import Client

from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest


class OllamaLLM(LLMClient):
    def __init__(
        self,
        model: str = "qwen3:4b-instruct-2507-q4_K_M",
        keep_alive: str = "5m",
        thinking_enabled: bool = False,
        context_window: int = 8192,
    ) -> None:
        self.model = model
        self.keep_alive = keep_alive
        self.thinking_enabled = thinking_enabled
        self.context_window = context_window
        self.max_output_tokens = 256
        # Bound transport waits and output without growing RAM/VRAM context.
        self.client = Client(host="http://localhost:11434", timeout=60.0)

    def set_keep_alive(self, keep_alive: str) -> None:
        self.keep_alive = keep_alive

    def warm_up(self) -> None:
        """Load the configured model in Ollama without touching memory/tools."""
        self.client.chat(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": "Responda somente: OK",
                }
            ],
            tools=[],
            keep_alive=self.keep_alive,
            think=False,
            options={"num_ctx": self.context_window, "num_predict": 4},
        )

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:

        response = self.client.chat(
            model=self.model,
            messages=messages,
            tools=tools or [],
            keep_alive=self.keep_alive,
            think=False,
            options={"num_ctx": self.context_window, "num_predict": self.max_output_tokens},
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

    def generate_final(
        self,
        messages: list[dict[str, Any]],
    ) -> LLMResponse:
        retry_messages = list(messages)
        retry_messages.append(
            {
                "role": "system",
                "content": (
                    "Formule somente a resposta final ao usuário. "
                    "Não exponha raciocínio, protocolo ou chamadas de tools."
                ),
            }
        )
        response = self.client.chat(
            model=self.model,
            messages=retry_messages,
            tools=[],
            keep_alive=self.keep_alive,
            think=False,
            options={"num_ctx": self.context_window, "num_predict": self.max_output_tokens},
            format={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        )
        try:
            payload = json.loads(response.message.content or "")
            answer = payload.get("answer", "")
        except (TypeError, ValueError, AttributeError):
            answer = ""
        return LLMResponse(content=answer if isinstance(answer, str) else "")
