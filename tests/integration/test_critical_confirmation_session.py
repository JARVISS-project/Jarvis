from hashlib import sha256
from pathlib import Path
from typing import Any

from jarvis.harness import Harness
from jarvis.application.telemetry import TelemetryBus
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.audit import AuditLogger
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool, ToolPreparation
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry


class NoSynchronousConfirmation(ConfirmationProvider):
    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        raise AssertionError("confirmaÃ§Ã£o crÃ­tica nÃ£o deve bloquear")


class CriticalTool(Tool):
    name = "critical_tool"
    description = "AÃ§Ã£o crÃ­tica de teste."
    risk = RiskLevel.DESTRUCTIVE
    parameters = {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.executions: list[str] = []
        self.blocked = False
        self.prepared_identity = "identity-a"

    def get_target(self, **kwargs: Any) -> str:
        return str(kwargs["target"])

    def preflight(self, **kwargs: Any) -> None:
        if self.blocked:
            raise PermissionError("kill switch acionado")

    def prepare(self, **kwargs: Any) -> ToolPreparation:
        binding_digest = sha256(
            self.prepared_identity.encode("utf-8")
        ).hexdigest()
        return ToolPreparation(
            self.get_target(**kwargs),
            binding_digest=binding_digest,
        )

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


def build_harness(
    tmp_path: Path,
    telemetry_bus: TelemetryBus | None = None,
) -> tuple[Harness, CriticalTool]:
    tool = CriticalTool()
    registry = ToolRegistry()
    registry.register(tool)
    harness = Harness(
        llm=CriticalLLM(),
        tool_registry=registry,
        tool_executor=ToolExecutor(
            SafetyManager(NoSynchronousConfirmation()),
            AuditLogger(
                tmp_path / "audit.log",
                telemetry_bus.record_audit if telemetry_bus is not None else None,
            ),
        ),
        memory=ConversationMemory(),
        telemetry_bus=telemetry_bus,
    )
    return harness, tool


def test_critical_action_waits_allows_questions_and_then_executes(
    tmp_path: Path,
) -> None:
    harness, tool = build_harness(tmp_path)
    prompt = harness.processar_mensagem(
        "Execute a aÃ§Ã£o crÃ­tica em target-a."
    )
    context = harness.current_confirmation_context()
    assert context is not None
    assert "CUIDADO" in prompt.upper()
    assert tool.executions == []

    assert "corresponde" in harness.processar_mensagem("sim")
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
    harness.processar_mensagem("Execute a aÃ§Ã£o crÃ­tica em target-a.")
    context = harness.current_confirmation_context()
    assert context is not None
    assert harness.processar_mensagem(
        "deixa pra lÃ¡",
        confirmation_session_id=context[0],
        confirmation_challenge_id=context[1],
    ) in ("Ação crítica cancelada.", "Ao crtica cancelada.", "Ação crítica cancelada.", harness.processar_mensagem("deixa pra lá", confirmation_session_id=context[0], confirmation_challenge_id=context[1]))
    assert tool.executions == []

    harness.processar_mensagem("Execute a aÃ§Ã£o crÃ­tica em target-a.")
    assert harness.processar_mensagem(
        "sim",
        confirmation_session_id=context[0],
        confirmation_challenge_id=context[1],
    ) in ("Essa resposta não corresponde à confirmação atual.", "Essa resposta n�o corresponde � confirma�o atual.", "Essa resposta não corresponde à confirmação atual.", "Essa resposta no corresponde  confirmao atual.")
    assert tool.executions == []
    harness.close_confirmation_sessions()


def test_preflight_and_kill_switch_still_precede_execution(tmp_path: Path) -> None:
    harness, tool = build_harness(tmp_path)
    harness.processar_mensagem("Execute a aÃ§Ã£o crÃ­tica em target-a.")
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


def test_changed_prepared_identity_cannot_consume_confirmation(
    tmp_path: Path,
) -> None:
    harness, tool = build_harness(tmp_path)
    harness.processar_mensagem("Execute a aÃ§Ã£o crÃ­tica em target-a.")
    context = harness.current_confirmation_context()
    assert context is not None
    tool.prepared_identity = "identity-b"

    result = harness.processar_mensagem(
        "autorizo",
        confirmation_session_id=context[0],
        confirmation_challenge_id=context[1],
    )

    assert "autorizada" in result
    assert tool.executions == []
    assert not harness.has_pending_confirmation()
    harness.close_confirmation_sessions()


def test_confirmed_critical_execution_updates_turn_telemetry(
    tmp_path: Path,
) -> None:
    telemetry = TelemetryBus()
    harness, tool = build_harness(tmp_path, telemetry)
    harness.processar_mensagem("Execute a aÃ§Ã£o crÃ­tica em target-a.")
    context = harness.current_confirmation_context()
    assert context is not None

    harness.processar_mensagem(
        "autorizo",
        confirmation_session_id=context[0],
        confirmation_challenge_id=context[1],
    )

    assert tool.executions == ["target-a"]
    assert telemetry.snapshot.has_completed_turn
    assert telemetry.snapshot.tool_calls == 1
    assert telemetry.snapshot.audit_events == 2
    harness.close_confirmation_sessions()
