from pathlib import Path
from typing import Any

from jarvis.context.file_reference_resolver import FileReferenceResolver
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.audit import AuditLogger
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry


class OneCallLLM(LLMClient):
    def __init__(self, path: str) -> None:
        self.responses = [
            LLMResponse(
                "",
                [ToolCallRequest("delete_file", {"path": path})],
            ),
            LLMResponse("concluído"),
        ]

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        return self.responses.pop(0)


class SequenceConfirmation(ConfirmationProvider):
    def __init__(self, answers: list[bool]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, RiskLevel]] = []

    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        self.calls.append((action, target, risk))
        return self.answers.pop(0)


def harness(
    tmp_path: Path,
    confirmation: SequenceConfirmation,
) -> Harness:
    guard = FileSystemGuard(tmp_path)
    registry = ToolRegistry()
    registry.register(DeleteFileTool(guard))
    return Harness(
        llm=OneCallLLM("teste segurança.txt"),
        tool_registry=registry,
        tool_executor=ToolExecutor(
            SafetyManager(confirmation), AuditLogger(tmp_path / "audit.log")
        ),
        memory=ConversationMemory(),
        file_reference_resolver=FileReferenceResolver(guard),
        target_confirmation=confirmation,
    )


def test_high_target_confirmation_is_the_only_confirmation(tmp_path: Path) -> None:
    target = tmp_path / "teste_seguranca.txt"
    target.write_text("x", encoding="utf-8")
    confirmation = SequenceConfirmation([True])
    result = harness(tmp_path, confirmation).processar_mensagem(
        "apague teste segurança.txt"
    )
    assert result == "concluído"
    assert not target.exists()
    assert [call[0] for call in confirmation.calls] == ["confirm_file_target"]


def test_denied_candidate_never_reaches_risk_or_tool(tmp_path: Path) -> None:
    target = tmp_path / "teste_seguranca.txt"
    target.write_text("x", encoding="utf-8")
    confirmation = SequenceConfirmation([False])
    result = harness(tmp_path, confirmation).processar_mensagem(
        "apague teste segurança.txt"
    )
    assert result == "Cancelado."
    assert target.exists()
    assert len(confirmation.calls) == 1


def test_target_confirmation_is_required_before_deletion(tmp_path: Path) -> None:
    target = tmp_path / "teste_seguranca.txt"
    target.write_text("x", encoding="utf-8")
    confirmation = SequenceConfirmation([True])
    harness(tmp_path, confirmation).processar_mensagem(
        "apague teste segurança.txt"
    )
    assert not target.exists()
    assert len(confirmation.calls) == 1
