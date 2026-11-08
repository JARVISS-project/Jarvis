from pathlib import Path

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


class TwoDeleteCalls(LLMClient):
    def __init__(self) -> None:
        self.paths = ["teste de segurança", "testesegurança.txt"]

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        return LLMResponse(
            "",
            [
                ToolCallRequest(
                    "delete_file", {"path": self.paths.pop(0)}
                )
            ],
        )


class FailTargetConfirmation(ConfirmationProvider):
    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        return False


class SpyDelete(DeleteFileTool):
    def __init__(self, guard: FileSystemGuard) -> None:
        super().__init__(guard)
        self.execute_calls = 0

    def execute(self, **kwargs):  # type: ignore[no-untyped-def]
        self.execute_calls += 1
        return super().execute(**kwargs)


def test_canceled_request_cannot_be_revived_by_hypothetical_message(
    tmp_path: Path,
) -> None:
    target = tmp_path / "teste_seguranca.txt"
    target.write_text("preservado", encoding="utf-8")
    guard = FileSystemGuard(tmp_path)
    tool = SpyDelete(guard)
    registry = ToolRegistry()
    registry.register(tool)
    confirmation = FailTargetConfirmation()
    harness = Harness(
        llm=TwoDeleteCalls(),
        tool_registry=registry,
        tool_executor=ToolExecutor(
            SafetyManager(confirmation), AuditLogger(tmp_path / "audit.log")
        ),
        memory=ConversationMemory(),
        file_reference_resolver=FileReferenceResolver(guard),
        target_confirmation=confirmation,
    )

    assert harness.processar_mensagem("apague teste de segurança") == "Cancelado."
    response = harness.processar_mensagem(
        "e se eu quero que você apague o arquivo testesegurança.txt"
    )

    assert response.startswith("Você quer que eu realmente exclua")
    assert tool.execute_calls == 0
    assert target.exists()
