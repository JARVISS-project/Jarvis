from pathlib import Path
from typing import Any

from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.audit import AuditLogger
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry


class NoSynchronousConfirmation(ConfirmationProvider):
    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        raise AssertionError("confirmação crítica não deve bloquear")


class CriticalTool(Tool):
    name = "critical_tool"
    description = "Ação crítica de teste."
    risk = RiskLevel.CRITICAL
    parameters = {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.executions: list[str] = []
        self.blocked = False

    def get_target(self, **kwargs: Any) -> str:
        return str(kwargs["target"])

    def preflight(self, **kwargs: Any) -> None:
        if self.blocked:
            raise PermissionError("kill switch acionado")

    def execute(self, **kwargs: Any) -> str:
        target = str(kwargs["target"])
        self.executions.append(target)
        return f"Executado em {target}."


class CriticalLLM(LLMClient):
    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        return LLMResponse(
            "",
            [ToolCallRequest("critical_tool", {"target": "target-a"})],
        )


def build_harness(tmp_path: Path) -> tuple[Harness, CriticalTool]:
    tool = CriticalTool()
    registry = ToolRegistry()
    registry.register(tool)
    harness = Harness(
        llm=CriticalLLM(),
        tool_registry=registry,
        tool_executor=ToolExecutor(
            SafetyManager(NoSynchronousConfirmation()),
            AuditLogger(tmp_path / "audit.log"),
        ),
        memory=ConversationMemory(),
    )
    return harness, tool


def test_critical_action_waits_allows_questions_and_then_executes(
    tmp_path: Path,
) -> None:
    harness, tool = build_harness(tmp_path)
    prompt = harness.processar_mensagem(
        "Execute a ação crítica em target-a."
    )
    context = harness.current_confirmation_context()
    assert context is not None
    assert "ALTO RISCO" in prompt.upper()
    assert tool.executions == []

    assert harness.processar_mensagem("sim") == (
        "Essa resposta não corresponde à confirmação atual."
    )
    assert tool.executions == []

    explanation = harness.processar_mensagem("Por que precisa confirmar?")
    assert "CRITICAL" in explanation
    assert harness.current_confirmation_context() == context

    result = harness.processar_mensagem(
        "sim, pode fazer",
        confirmation_session_id=context[0],
        confirmation_challenge_id=context[1],
    )
    assert result == "Executado em target-a."
    assert tool.executions == ["target-a"]
    assert not harness.has_pending_confirmation()
    harness.close_confirmation_sessions()


def test_denial_and_stale_confirmation_never_execute(tmp_path: Path) -> None:
    harness, tool = build_harness(tmp_path)
    harness.processar_mensagem("Execute a ação crítica em target-a.")
    context = harness.current_confirmation_context()
    assert context is not None
    assert harness.processar_mensagem(
        "deixa pra lá",
        confirmation_session_id=context[0],
        confirmation_challenge_id=context[1],
    ) == "Ação crítica cancelada."
    assert tool.executions == []

    harness.processar_mensagem("Execute a ação crítica em target-a.")
    assert harness.processar_mensagem(
        "sim",
        confirmation_session_id=context[0],
        confirmation_challenge_id=context[1],
    ) == "Essa resposta não corresponde à confirmação atual."
    assert tool.executions == []
    harness.close_confirmation_sessions()


def test_preflight_and_kill_switch_still_precede_execution(tmp_path: Path) -> None:
    harness, tool = build_harness(tmp_path)
    harness.processar_mensagem("Execute a ação crítica em target-a.")
    context = harness.current_confirmation_context()
    assert context is not None
    tool.blocked = True

    result = harness.processar_mensagem(
        "autorizo",
        confirmation_session_id=context[0],
        confirmation_challenge_id=context[1],
    )

    assert "kill switch" in result
    assert tool.executions == []
    harness.close_confirmation_sessions()
