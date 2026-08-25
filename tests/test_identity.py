from copy import deepcopy
from typing import Any

from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse
from jarvis.memory.conversation import ConversationMemory
from jarvis.tools.registry import ToolRegistry


class CapturingLLM(LLMClient):
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[dict[str, Any]] = []

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.messages = deepcopy(messages)
        return LLMResponse(self.content)


class UnusedExecutor:
    pass


def make_harness(llm: CapturingLLM) -> Harness:
    return Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=UnusedExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
    )


def test_identity_context_is_sent_for_who_are_you_question() -> None:
    llm = CapturingLLM("Sou Jarvis.")
    harness = make_harness(llm)

    harness.processar_mensagem("Quem é você?")

    system = llm.messages[0]
    assert system["role"] == "system"
    assert "Você é Jarvis" in system["content"]
    assert "assistente pessoal local" in system["content"]
    assert llm.messages[-1] == {
        "role": "user",
        "content": "Quem é você?",
    }


def test_complete_response_wrapper_is_removed_from_final_answer() -> None:
    harness = make_harness(
        CapturingLLM("<response>Resposta direta.</response>")
    )

    result = harness.processar_mensagem("Responda diretamente")

    assert result == "Resposta direta."
    assert "<response>" not in result
