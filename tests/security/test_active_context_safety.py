from pathlib import Path
from typing import Any

from jarvis.context.note_reference_resolver import NoteReferenceResolver
from jarvis.context.recent import RecentContext
from jarvis.harness import Harness
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.confirmation_intent import ConfirmationResult
from jarvis.security.confirmation_request import ConfirmationRequest
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.tools.obsidian import ObsidianAppendNoteTool
from jarvis.tools.registry import ToolRegistry


class UnusedLLM(LLMClient):
    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        return LLMResponse("unused")


class TargetOnlyConfirmation(ConfirmationProvider):
    def __init__(self) -> None:
        self.requests: list[ConfirmationRequest] = []

    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        return False

    def confirm_result(self, request: ConfirmationRequest) -> ConfirmationResult:
        self.requests.append(request)
        return ConfirmationResult(target_confirmed=True)


class NoGrantExecutor:
    def execute(self, tool: Any, **kwargs: Any) -> str:
        raise AssertionError("a resolução de contexto não deve executar")


def test_context_resolves_medium_note_target_but_creates_no_authorization(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    (vault_path / "Segunda.md").write_text("texto", encoding="utf-8")
    vault = ObsidianVault(vault_path, FileSystemGuard(vault_path))
    recent = RecentContext()
    recent.set("last_active_note", "Segunda.md")
    confirmation = TargetOnlyConfirmation()
    harness = Harness(
        llm=UnusedLLM(),
        tool_registry=ToolRegistry(),
        tool_executor=NoGrantExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        recent_context=recent,
        note_reference_resolver=NoteReferenceResolver(vault, recent),
        target_confirmation=confirmation,
    )

    resolved, response, grant, interaction_id = harness._resolve_note_reference(
        ObsidianAppendNoteTool(vault),
        {"path": "ela", "content": "mais"},
    )

    assert resolved["path"] == "Segunda.md"
    assert response is None
    assert grant is None
    assert interaction_id is None
    assert len(confirmation.requests) == 1


def test_ambiguous_note_cleanup_never_calls_llm_or_creates_grant() -> None:
    llm = UnusedLLM()
    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=NoGrantExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
    )

    response = harness.processar_mensagem("limpe minhas notas")

    assert "Qual escopo" in response
