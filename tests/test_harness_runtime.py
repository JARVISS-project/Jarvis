from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from jarvis.config import JarvisConfig
from jarvis.application.telemetry import TelemetryBus
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.state import RuntimeState
from jarvis.tools.get_datetime import GetDateTimeTool
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
    telemetry_bus: TelemetryBus | None = None,
) -> Harness:
    return Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=memory or ConversationMemory(),
        memory_store=store or InMemoryMemoryStore(),
        config=config,
        resource_manager=manager,
        telemetry_bus=telemetry_bus,
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


def test_completed_turn_publishes_aggregate_telemetry() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    telemetry = TelemetryBus()
    harness = make_harness(manager, llm, telemetry_bus=telemetry)

    harness.processar_mensagem("Olá")

    assert telemetry.snapshot.has_completed_turn
    assert telemetry.snapshot.llm_calls == 1
    assert telemetry.snapshot.tool_calls == 0
    assert telemetry.snapshot.turn_ms >= 0


class DirectToolExecutor:
    def execute(self, tool: Any, **kwargs: Any) -> str:
        return tool.execute(**kwargs)


def test_time_fast_path_skips_llm_and_uses_datetime_tool() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    registry = ToolRegistry()
    current = datetime(
        2026, 8, 26, 10, 36,
        tzinfo=timezone(timedelta(hours=-3)),
    )
    registry.register(GetDateTimeTool(clock=lambda: current))
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=DirectToolExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        resource_manager=manager,
    )

    result = harness.processar_mensagem("Estou perguntando que odas são.")

    assert result == "Agora são 10 horas e 36 minutos."
    assert llm.calls == 0
    latency = harness.get_last_latency()
    assert latency["llm_calls"] == 0
    assert latency["tool_calls"] == 1


def test_harness_can_learn_last_successful_tool_trace_as_skill() -> None:
    from jarvis.skills.store import InMemorySkillStore
    from jarvis.tools.execution_result import ToolExecutionResult

    class ResultExecutor:
        def execute_with_result(self, tool: Any, **kwargs: Any) -> ToolExecutionResult:
            return ToolExecutionResult(tool.execute(**kwargs), "success", True)

    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    registry = ToolRegistry()
    current = datetime(
        2026, 8, 26, 10, 36,
        tzinfo=timezone(timedelta(hours=-3)),
    )
    registry.register(GetDateTimeTool(clock=lambda: current))
    skills = InMemorySkillStore()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=ResultExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        memory_store=InMemoryMemoryStore(),
        config=JarvisConfig(),
        resource_manager=manager,
        skill_store=skills,
    )

    harness.processar_mensagem("Qual a data de hoje?")
    learned = harness.processar_mensagem('/skill learn "Consultar data"')

    assert "aprendida com 1 etapa" in learned
    assert skills.get("Consultar data") is not None
    assert "Consultar data" in harness.processar_mensagem("/skills")


def test_natural_skill_learning_phrase_is_deterministic() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)
    harness.last_tool_trace = []

    response = harness.processar_mensagem(
        "Agora aprenda esse procedimento de como Minha Rotina"
    )

    assert response == "Não há um procedimento recente para aprender."
    assert llm.calls == 0


def test_learned_skill_can_be_run_by_natural_explicit_name() -> None:
    from jarvis.security.risk import RiskLevel
    from jarvis.skills.models import SkillDefinition, SkillStep
    from jarvis.skills.store import InMemorySkillStore
    from jarvis.tools.base import Tool
    from jarvis.tools.execution_result import ToolExecutionResult

    class PingTool(Tool):
        name = "ping_tool"
        description = "ping"
        risk = RiskLevel.LOW
        parameters = {"type": "object", "properties": {}, "required": []}

        def get_target(self, **_kwargs: Any) -> str:
            return "ping"

        def execute(self, **_kwargs: Any) -> str:
            return "pong"

    class ResultExecutor:
        def execute_with_result(self, tool: Any, **kwargs: Any) -> ToolExecutionResult:
            return ToolExecutionResult(tool.execute(**kwargs), "success", True)

    registry = ToolRegistry()
    registry.register(PingTool())
    store = InMemorySkillStore()
    store.save(
        SkillDefinition(
            name="Ping",
            steps=(SkillStep("ping_tool", {}),),
        )
    )
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=ResultExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        memory_store=InMemoryMemoryStore(),
        resource_manager=manager,
        skill_store=store,
    )

    response = harness.processar_mensagem("Agora, executa esse esquivo aí. Ping.md")

    assert response == "Pronto."
    assert llm.calls == 0
    assert harness.last_tool_trace[0].tool_name == "ping_tool"
    assert harness.get_last_latency()["tool_calls"] == 1


def test_skill_learning_de_como_creates_real_skill_without_llm() -> None:
    from jarvis.security.risk import RiskLevel
    from jarvis.skills.models import ToolTraceStep
    from jarvis.skills.store import InMemorySkillStore
    from jarvis.tools.base import Tool

    class PingTool(Tool):
        name = "ping_tool"
        description = "ping"
        risk = RiskLevel.LOW
        parameters = {"type": "object", "properties": {}, "required": []}

        def get_target(self, **_kwargs: Any) -> str:
            return "ping"

        def execute(self, **_kwargs: Any) -> str:
            return "pong"

    registry = ToolRegistry()
    registry.register(PingTool())
    store = InMemorySkillStore()
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        memory_store=InMemoryMemoryStore(),
        resource_manager=manager,
        skill_store=store,
    )
    harness.last_tool_trace = [ToolTraceStep("ping_tool", {})]
    harness.last_tool_trace_request = "faça ping"

    response = harness.processar_mensagem(
        "Agora aprenda esse procedimento de como Ler Jarvis"
    )

    assert "Skill 'Ler Jarvis' aprendida" in response
    assert store.get("Ler Jarvis") is not None
    assert llm.calls == 0


def test_skill_run_accepts_esquivo_and_small_name_variation() -> None:
    from jarvis.security.risk import RiskLevel
    from jarvis.skills.models import SkillDefinition, SkillStep
    from jarvis.skills.store import InMemorySkillStore
    from jarvis.tools.base import Tool
    from jarvis.tools.execution_result import ToolExecutionResult

    class PingTool(Tool):
        name = "ping_tool"
        description = "ping"
        risk = RiskLevel.LOW
        parameters = {"type": "object", "properties": {}, "required": []}

        def get_target(self, **_kwargs: Any) -> str:
            return "ping"

        def execute(self, **_kwargs: Any) -> str:
            return "pong"

    class ResultExecutor:
        def execute_with_result(self, tool: Any, **kwargs: Any) -> ToolExecutionResult:
            return ToolExecutionResult(tool.execute(**kwargs), "success", True)

    registry = ToolRegistry()
    registry.register(PingTool())
    store = InMemorySkillStore()
    store.save(
        SkillDefinition(
            name="Ler Jarvis",
            steps=(SkillStep("ping_tool", {}),),
        )
    )
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=ResultExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        memory_store=InMemoryMemoryStore(),
        resource_manager=manager,
        skill_store=store,
    )

    response = harness.processar_mensagem(
        "Agora executa esse esquivo aí. Lê Jarvis."
    )

    assert response == "Pronto."
    assert llm.calls == 0
    assert harness.last_tool_trace[0].tool_name == "ping_tool"
    assert harness.get_last_latency()["tool_calls"] == 1


def test_unparsed_skill_control_does_not_fall_through_to_llm() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)

    response = harness.processar_mensagem(
        "Aprenda esse procedimento por favor"
    )

    assert "não consegui identificar o nome" in response.casefold()
    assert llm.calls == 0


def test_natural_doctor_voice_phrase_bypasses_llm() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)

    result = harness.processar_mensagem(
        "Me diz aí como é que tá teu doctor cara?"
    )

    assert result.startswith("Jarvis Doctor")
    assert llm.calls == 0


def test_garbled_run_doctor_phrase_still_bypasses_llm() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)

    result = harness.processar_mensagem(
        "No caso, roda aí um barro invertido a doctor."
    )

    assert result.startswith("Jarvis Doctor")
    assert llm.calls == 0


def test_skill_learning_accepts_named_as_phrase_from_voice_log() -> None:
    from jarvis.skills.models import ToolTraceStep
    from jarvis.skills.store import InMemorySkillStore

    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    store = InMemorySkillStore()
    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        memory_store=InMemoryMemoryStore(),
        resource_manager=manager,
        skill_store=store,
    )
    # No executable trace is available, so the important behavior here is that
    # the natural phrase reaches the deterministic learner instead of the LLM.
    harness.last_tool_trace = []

    response = harness.processar_mensagem(
        "Pronto! Aprenda o procedimento de organizar o texto em uma nota "
        "como Organize Nota."
    )

    assert response == "Não há um procedimento recente para aprender."
    assert llm.calls == 0


def test_natural_skill_inventory_is_grounded_in_store() -> None:
    from jarvis.skills.models import SkillDefinition
    from jarvis.skills.store import InMemorySkillStore

    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    store = InMemorySkillStore()
    store.save(SkillDefinition(name="Organizar nota", steps=()))
    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        memory_store=InMemoryMemoryStore(),
        resource_manager=manager,
        skill_store=store,
    )

    response = harness.processar_mensagem("Me diz ai quais skills você tem.")

    assert response == "Tenho estas Skills: Organizar nota."
    assert llm.calls == 0


def test_natural_doctor_voice_mode_uses_compact_summary(
    monkeypatch,
) -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)
    monkeypatch.setattr(
        harness.doctor,
        "voice_summary",
        lambda: "Tudo certo por aqui.",
    )

    result = harness.processar_mensagem(
        "Me diz aí como está teu doctor?",
        response_mode="voice",
    )

    assert result == "Tudo certo por aqui."
    assert llm.calls == 0
