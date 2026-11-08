from typing import Any

from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool
from jarvis.tools.registry import ToolRegistry


class RuntimeProbeTool(Tool):
    name = "get_runtime_info"
    description = "Retorna um resumo seguro do runtime."
    risk = RiskLevel.READ_ONLY
    parameters = {"type": "object", "properties": {}, "required": []}

    def get_target(self, **_kwargs: Any) -> str:
        return "runtime"

    def execute(self, **_kwargs: Any) -> str:
        return "runtime ok"


class Executor:
    def execute(self, tool: Tool, **kwargs: Any) -> str:
        return str(tool.execute(**kwargs))


class RetryLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return LLMResponse("Vou verificar para você.")
        if self.calls == 2:
            return LLMResponse(
                "",
                [ToolCallRequest("get_runtime_info", {})],
            )
        return LLMResponse("O runtime respondeu normalmente.")


def test_action_request_gets_one_tool_call_retry_before_giving_up() -> None:
    registry = ToolRegistry()
    registry.register(RuntimeProbeTool())
    llm = RetryLLM()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=Executor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
    )

    response = harness.processar_mensagem("Verifica o sistema aí pra mim")

    assert response == "O runtime respondeu normalmente."
    assert llm.calls == 3
    assert [step.tool_name for step in harness.last_tool_trace] == [
        "get_runtime_info"
    ]


class HallucinatingLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return LLMResponse(
            "Encontrei os arquivos e listei as notas para você."
        )


def test_action_request_never_accepts_fake_tool_result() -> None:
    registry = ToolRegistry()
    registry.register(RuntimeProbeTool())
    llm = HallucinatingLLM()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=Executor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
    )

    response = harness.processar_mensagem("Testa alguma tool aí")

    assert response.startswith("Entendi que você quer uma ação real")
    assert llm.calls == 2
    assert harness.last_tool_trace == []
