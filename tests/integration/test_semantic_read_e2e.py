from datetime import datetime, timedelta, timezone

from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.get_datetime import GetDateTimeTool
from jarvis.tools.get_runtime_info import GetRuntimeInfoTool
from jarvis.tools.registry import ToolRegistry
import pytest


class NeverLlm(LLMClient):
    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        raise AssertionError("valid semantic read must not reach the LLM")


class CountingExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute_with_result(self, tool, *, _interaction_id=None, **kwargs):
        self.calls += 1
        return ToolExecutionResult(tool.execute(**kwargs), "success", True)


@pytest.mark.parametrize("transcript", [
    "Que horas são?", "Qual é a data de hoje?",
    "Qual é o dia da semana?", "Qual é o status do runtime?",
])
def test_validated_transcript_reaches_real_tool_response_and_tts_sink(
    tmp_path, transcript,
) -> None:
    config = JarvisConfig(settings_path=tmp_path / "missing.json")
    config.semantic_read_tools_enabled = True
    registry = ToolRegistry()
    registry.register(GetDateTimeTool(clock=lambda: datetime(
        2026, 9, 28, 9, 42, tzinfo=timezone(timedelta(hours=-3)),
    )))
    resource_manager = ResourceManager()
    registry.register(GetRuntimeInfoTool(config, resource_manager))
    executor = CountingExecutor()
    harness = Harness(
        llm=NeverLlm(), tool_registry=registry,
        tool_executor=executor,  # type: ignore[arg-type]
        memory=ConversationMemory(), config=config,
        resource_manager=resource_manager,
    )
    tts_sink: list[str] = []

    response = harness.processar_mensagem(
        transcript, manage_runtime=False, response_mode="voice",
    )
    tts_sink.append(response)

    assert executor.calls == 1
    assert response
    assert tts_sink == [response]
    assert harness.get_last_latency()["tool_calls"] == 1


def test_negated_transcript_stops_before_tool_and_llm(tmp_path) -> None:
    config = JarvisConfig(settings_path=tmp_path / "missing.json")
    config.semantic_read_tools_enabled = True
    registry = ToolRegistry(); registry.register(GetDateTimeTool())
    executor = CountingExecutor()
    harness = Harness(
        llm=NeverLlm(), tool_registry=registry,
        tool_executor=executor,  # type: ignore[arg-type]
        memory=ConversationMemory(), config=config,
    )
    response = harness.processar_mensagem("Não me diga que horas são")
    assert "Não executei" in response and executor.calls == 0
    named = harness.processar_mensagem("execute get_datetime")
    assert "Não executei" in named and executor.calls == 0
