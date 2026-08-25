from pathlib import Path
from typing import Any

from jarvis.context.active import ActiveContext
from jarvis.context.note_reference_resolver import NoteReferenceResolver
from jarvis.context.recent import RecentContext
from jarvis.harness import Harness
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.filesystem import FileSystemGuard
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.list_files import ListFilesTool
from jarvis.tools.obsidian import (
    ObsidianOpenNoteTool,
    ObsidianReadNoteTool,
    ObsidianSearchNotesTool,
)
from jarvis.tools.registry import ToolRegistry


class ScriptedLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.messages: list[list[dict[str, Any]]] = []

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.messages.append(messages)
        return self.responses.pop(0)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_with_result(
        self, tool: Any, **arguments: Any
    ) -> ToolExecutionResult:
        self.calls.append((tool.name, arguments))
        if tool.name == "obsidian_search_notes":
            content = (
                "- Teste.md | score=3 | primeiro\n"
                "- Teste para Jarvis.md | score=2 | segundo"
            )
        elif tool.name == "obsidian_open_note":
            content = "Nota aberta no Obsidian."
        else:
            content = "conteúdo da nota"
        return ToolExecutionResult(content, "success", True)


def tool_call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse("", [ToolCallRequest(name, arguments)])


def build_context_harness(
    tmp_path: Path, responses: list[LLMResponse]
) -> tuple[Harness, RecordingExecutor, ScriptedLLM]:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    (vault_path / "Teste.md").write_text("primeiro", encoding="utf-8")
    (vault_path / "Teste para Jarvis.md").write_text(
        "segundo", encoding="utf-8"
    )
    vault = ObsidianVault(vault_path, FileSystemGuard(vault_path))
    registry = ToolRegistry()
    registry.register(ListFilesTool(FileSystemGuard(tmp_path)))
    registry.register(ObsidianSearchNotesTool(vault))
    registry.register(ObsidianOpenNoteTool(vault))
    registry.register(ObsidianReadNoteTool(vault))
    recent = RecentContext()
    llm = ScriptedLLM(responses)
    executor = RecordingExecutor()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=executor,  # type: ignore[arg-type]
        memory=ConversationMemory(),
        recent_context=recent,
        note_reference_resolver=NoteReferenceResolver(vault, recent),
    )
    return harness, executor, llm


def test_my_notes_search_is_deterministically_routed_to_obsidian(
    tmp_path: Path,
) -> None:
    harness, executor, llm = build_context_harness(
        tmp_path,
        [tool_call("list_files", path="tests"), LLMResponse("Encontrei.")],
    )

    harness.processar_mensagem("Procure as minhas notas sobre testes.")

    assert executor.calls == [
        ("obsidian_search_notes", {"query": "testes"})
    ]
    assert harness.context_router.state.active is ActiveContext.OBSIDIAN
    assert "Current source context: OBSIDIAN" in llm.messages[0][0]["content"]


def test_correction_then_ordinal_and_pronoun_use_only_note_context(
    tmp_path: Path,
) -> None:
    harness, executor, _llm = build_context_harness(
        tmp_path,
        [
            LLMResponse("Entendido."),
            tool_call("list_files", path="tests"),
            LLMResponse("Encontrei."),
            tool_call("obsidian_open_note", path="a segunda"),
            LLMResponse("Aberta."),
            tool_call("read_file", path="ela"),
            LLMResponse("Lida."),
        ],
    )

    harness.processar_mensagem("Não no diretório, eu quero no Obsidian.")
    harness.processar_mensagem("Procure teste.")
    harness.processar_mensagem("Abra a segunda.")
    harness.processar_mensagem("Leia ela.")

    assert executor.calls == [
        ("obsidian_search_notes", {"query": "teste"}),
        ("obsidian_open_note", {"path": "Teste para Jarvis.md"}),
        ("obsidian_read_note", {"path": "Teste para Jarvis.md"}),
    ]
    assert harness.recent_context.get("last_active_note") == (
        "Teste para Jarvis.md"
    )
    assert harness.recent_context.get("last_file_results") is None
