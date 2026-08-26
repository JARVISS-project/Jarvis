from typing import Any

import pytest

from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.state import RuntimeState
from jarvis.tools.registry import ToolRegistry


class RuntimeAwareLLM(LLMClient):
    def __init__(
        self,
        resource_manager: ResourceManager,
        error: Exception | None = None,
    ) -> None:
        self.resource_manager = resource_manager
        self.error = error
        self.calls = 0
        self.states_during_generate: list[RuntimeState] = []
        self.keep_alive_values: list[str] = []

    def set_keep_alive(self, keep_alive: str) -> None:
        self.keep_alive_values.append(keep_alive)

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.states_during_generate.append(self.resource_manager.state)
        if self.error is not None:
            raise self.error
        return LLMResponse(content="Resposta")


class FakeToolExecutor:
    def execute(self, tool: Any, **kwargs: Any) -> str:
        return "resultado"


def make_harness(
    manager: ResourceManager,
    llm: RuntimeAwareLLM,
    memory: ConversationMemory | None = None,
    store: InMemoryMemoryStore | None = None,
    config: JarvisConfig | None = None,
) -> Harness:
    return Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=memory or ConversationMemory(),
        memory_store=store or InMemoryMemoryStore(),
        config=config,
        resource_manager=manager,
    )


def test_normal_interaction_returns_to_idle() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)

    harness.processar_mensagem("Olá")

    assert llm.states_during_generate == [RuntimeState.ACTIVE]
    assert manager.state is RuntimeState.IDLE


def test_normal_interaction_returns_to_game_mode() -> None:
    manager = ResourceManager()
    manager.enable_game_mode()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)

    harness.processar_mensagem("Olá")

    assert llm.states_during_generate == [RuntimeState.ACTIVE]
    assert manager.state is RuntimeState.GAME_MODE


def test_exception_does_not_leave_runtime_active() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager, error=RuntimeError("falha"))
    harness = make_harness(manager, llm)

    with pytest.raises(RuntimeError, match="falha"):
        harness.processar_mensagem("Olá")

    assert manager.state is RuntimeState.IDLE


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/gamemode on", "Game Mode ativado."),
        ("/gamemode off", "Game Mode desativado."),
        ("/status", "Estado:"),
    ],
)
def test_runtime_commands_do_not_call_llm_or_enter_conversation(
    command: str,
    expected: str,
) -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    memory = ConversationMemory()
    harness = make_harness(manager, llm, memory=memory)

    result = harness.processar_mensagem(command)

    assert expected in result
    assert llm.calls == 0
    assert memory.get_history() == []


def test_status_reports_safe_runtime_summary() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    memory = ConversationMemory()
    memory.add_user_message("Mensagem")
    store = InMemoryMemoryStore()
    store.add("Conteúdo que não deve aparecer", "user")
    config = JarvisConfig(model_name="modelo-local")
    harness = make_harness(manager, llm, memory, store, config)

    result = harness.processar_mensagem("/status")

    assert "Estado: IDLE" in result
    assert "Game Mode: desativado" in result
    assert "Modelo: modelo-local" in result
    assert "Conversa: 1" in result
    assert "Memórias: 1" in result
    assert "Conteúdo que não deve aparecer" not in result
    assert llm.calls == 0


def test_normal_mode_uses_normal_keep_alive_policy() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    config = JarvisConfig(ollama_keep_alive_normal="7m")
    harness = make_harness(manager, llm, config=config)

    harness.processar_mensagem("Olá")

    assert llm.keep_alive_values == ["7m"]


def test_game_mode_uses_short_keep_alive_policy() -> None:
    manager = ResourceManager()
    manager.enable_game_mode()
    llm = RuntimeAwareLLM(manager)
    config = JarvisConfig(ollama_keep_alive_game_mode="10s")
    harness = make_harness(manager, llm, config=config)

    harness.processar_mensagem("Olá")

    assert llm.keep_alive_values == ["10s"]


def test_active_voice_pipeline_can_delegate_runtime_ownership() -> None:
    manager = ResourceManager()
    manager.activate()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)

    harness.processar_mensagem("Olá", manage_runtime=False)

    assert llm.states_during_generate == [RuntimeState.ACTIVE]
    assert manager.state is RuntimeState.ACTIVE


def test_harness_records_last_latency_and_reports_it() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)

    harness.processar_mensagem("Olá")

    latency = harness.get_last_latency()
    assert latency["harness_seconds"] >= 0.0
    assert latency["llm_seconds"] >= 0.0
    assert latency["llm_calls"] == 1
    assert latency["tool_calls"] == 0

    report = harness.processar_mensagem("/latency")
    assert "Última interação:" in report
    assert "LLM" in report
    assert "Tools" in report
    assert llm.calls == 1
