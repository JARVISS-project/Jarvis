from pathlib import Path

from jarvis.context.file_reference_resolver import FileReferenceResolver
from jarvis.context.recent import RecentContext
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.audit import AuditLogger
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.confirmation_intent import ConfirmationInterpreter
from jarvis.security.confirmation_request import ConfirmationRequest
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry


class AmbiguousDeleteLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return LLMResponse(
            "",
            [ToolCallRequest("delete_file", {"path": "algum arquivo"})],
        )


class Answers(ConfirmationProvider):
    def __init__(self, values: list[bool]) -> None:
        self.values = values
        self.actions: list[str] = []

    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        self.actions.append(action)
        return self.values.pop(0)


class NaturalAnswer(Answers):
    def __init__(self, phrase: str) -> None:
        super().__init__([])
        self.phrase = phrase

    def confirm_result(self, request: ConfirmationRequest):
        self.actions.append(request.action)
        return ConfirmationInterpreter().interpret_result(
            self.phrase, request.purpose
        )


def make_harness(
    tmp_path: Path, answers: list[bool]
) -> tuple[Harness, AmbiguousDeleteLLM, Answers, ConversationMemory]:
    guard = FileSystemGuard(tmp_path)
    registry = ToolRegistry()
    registry.register(DeleteFileTool(guard))
    llm = AmbiguousDeleteLLM()
    confirmation = Answers(answers)
    memory = ConversationMemory()
    recent = RecentContext()
    recent.set(
        "last_search_results",
        ["teste_seguranca.txt", "README.md", "pyproject.toml"],
    )
    from jarvis.security.policy import SecurityPolicy
    class PermissivePolicy(SecurityPolicy):
        def is_allowed(self, risk): return True
        def requires_confirmation(self, risk): return False
    safety = SafetyManager(confirmation)
    safety.policy = PermissivePolicy()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=ToolExecutor(
            safety, AuditLogger(tmp_path / "audit.log")
        ),
        memory=memory,
        recent_context=recent,
        file_reference_resolver=FileReferenceResolver(guard, recent),
        target_confirmation=confirmation,
    )
    return harness, llm, confirmation, memory


def make_harness_with_provider(
    tmp_path: Path, confirmation: ConfirmationProvider
) -> tuple[Harness, AmbiguousDeleteLLM]:
    guard = FileSystemGuard(tmp_path)
    registry = ToolRegistry()
    registry.register(DeleteFileTool(guard))
    llm = AmbiguousDeleteLLM()
    recent = RecentContext()
    recent.set("last_search_results", ["teste_seguranca.txt"])
    from jarvis.security.policy import SecurityPolicy
    class PermissivePolicy(SecurityPolicy):
        def is_allowed(self, risk): return True
        def requires_confirmation(self, risk): return False
    safety = SafetyManager(confirmation)
    safety.policy = PermissivePolicy()
    return Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=ToolExecutor(
            safety, AuditLogger(tmp_path / "audit.log")
        ),
        memory=ConversationMemory(),
        recent_context=recent,
        file_reference_resolver=FileReferenceResolver(guard, recent),
        target_confirmation=confirmation,
    ), llm


def test_followup_fills_slot_without_llm_and_confirms_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "teste_seguranca.txt"
    target.write_text("x", encoding="utf-8")
    harness, llm, confirmation, memory = make_harness(
        tmp_path, [True]
    )
    assert "Qual arquivo" in harness.processar_mensagem("Apague algum arquivo")
    memory_count = memory.count()
    result = harness.processar_mensagem("teste de segurança ponto textista")
    assert "excluído" in result
    assert not target.exists()
    assert llm.calls == 1
    assert memory.count() == memory_count
    assert confirmation.actions == ["confirm_file_target"]


def test_pending_cancellation_does_not_call_llm_or_tool(tmp_path: Path) -> None:
    target = tmp_path / "teste_seguranca.txt"
    target.write_text("x", encoding="utf-8")
    harness, llm, _, memory = make_harness(tmp_path, [])
    harness.processar_mensagem("Apague algum arquivo")
    memory_count = memory.count()
    assert harness.processar_mensagem("deixa pra lá") == "Cancelado."
    assert target.exists()
    assert llm.calls == 1
    assert memory.count() == memory_count


def test_target_denial_prevents_security_confirmation(tmp_path: Path) -> None:
    target = tmp_path / "teste_seguranca.txt"
    target.write_text("x", encoding="utf-8")
    harness, _, confirmation, _ = make_harness(tmp_path, [False])
    harness.processar_mensagem("Apague algum arquivo")
    assert harness.processar_mensagem("teste de segurança") == "Cancelado."
    assert target.exists()
    assert confirmation.actions == ["confirm_file_target"]


def test_compound_target_and_action_avoids_redundant_round(tmp_path: Path) -> None:
    target = tmp_path / "teste_seguranca.txt"
    target.write_text("x", encoding="utf-8")
    confirmation = NaturalAnswer("é esse mesmo, pode apagar")
    harness, llm = make_harness_with_provider(tmp_path, confirmation)
    harness.processar_mensagem("Apague algum arquivo")
    memory_count = harness.memory.count()
    result = harness.processar_mensagem("teste de segurança")
    assert "excluído" in result
    assert not target.exists()
    assert confirmation.actions == ["confirm_file_target"]
    assert llm.calls == 1
    assert harness.memory.count() == memory_count
