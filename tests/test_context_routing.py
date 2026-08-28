from pathlib import Path
from typing import Any

from jarvis.context.active import ActiveContext
from jarvis.context.file_reference_resolver import FileReferenceResolver
from jarvis.context.note_reference_resolver import NoteReferenceResolver
from jarvis.context.recent import RecentContext
from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.confirmation_intent import ConfirmationResult
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.list_files import ListFilesTool
from jarvis.tools.edit_file import EditFileTool
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.obsidian import (
    ObsidianAppendNoteTool,
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
        filtered = {k: v for k, v in arguments.items() if k != "_interaction_id"}
        self.calls.append((tool.name, filtered))
        if tool.name == "obsidian_search_notes":
            content = (
                "- Teste.md | score=3 | primeiro\n"
                "- Teste para Jarvis.md | score=2 | segundo"
            )
        elif tool.name == "obsidian_open_note":
            content = "Nota aberta no Obsidian."
        elif tool.name == "list_files":
            content = "[FILE] pyproject.toml"
        else:
            content = "conteúdo da nota"
        return ToolExecutionResult(content, "success", True)


class TargetConfirmation:
    def confirm_result(self, request: Any) -> ConfirmationResult:
        return ConfirmationResult(target_confirmed=True)


def tool_call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse("", [ToolCallRequest(name, arguments)])


def build_context_harness(
    tmp_path: Path,
    responses: list[LLMResponse],
    *,
    obsidian_write_enabled: bool = False,
    register_obsidian_write: bool = False,
) -> tuple[Harness, RecordingExecutor, ScriptedLLM]:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    (vault_path / "Teste.md").write_text("primeiro", encoding="utf-8")
    (vault_path / "Teste para Jarvis.md").write_text(
        "segundo", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]", encoding="utf-8"
    )
    vault = ObsidianVault(vault_path, FileSystemGuard(vault_path))
    filesystem = FileSystemGuard(tmp_path)
    registry = ToolRegistry()
    registry.register(ListFilesTool(filesystem))
    registry.register(EditFileTool(filesystem))
    registry.register(ReadFileTool(filesystem))
    registry.register(ObsidianSearchNotesTool(vault))
    registry.register(ObsidianOpenNoteTool(vault))
    registry.register(ObsidianReadNoteTool(vault))
    if register_obsidian_write:
        registry.register(ObsidianAppendNoteTool(vault))
    recent = RecentContext()
    llm = ScriptedLLM(responses)
    executor = RecordingExecutor()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=executor,  # type: ignore[arg-type]
        memory=ConversationMemory(),
        config=JarvisConfig(
            settings_path=tmp_path / "settings.json",
            obsidian_enabled=True,
            obsidian_vault_path=vault_path,
            obsidian_write_enabled=obsidian_write_enabled,
        ),
        recent_context=recent,
        file_reference_resolver=FileReferenceResolver(filesystem, recent),
        note_reference_resolver=NoteReferenceResolver(vault, recent),
        target_confirmation=TargetConfirmation(),  # type: ignore[arg-type]
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


def test_main_context_flow_keeps_entities_separate_and_switches_source(
    tmp_path: Path,
) -> None:
    harness, executor, _llm = build_context_harness(
        tmp_path,
        [
            tool_call("list_files", path="tests"),
            LLMResponse("Encontrei."),
            tool_call("obsidian_open_note", path="a segunda"),
            LLMResponse("Aberta."),
            tool_call("read_file", path="ela"),
            LLMResponse("Assunto identificado."),
            tool_call("obsidian_search_notes", query="teste"),
            LLMResponse("Há outra parecida."),
            tool_call("list_files", path="."),
            LLMResponse("Encontrei o arquivo."),
            tool_call("read_file", path="ele"),
            LLMResponse("Arquivo lido."),
        ],
    )

    harness.processar_mensagem("Procure minhas notas sobre testes.")
    harness.processar_mensagem("Abra a segunda.")
    harness.processar_mensagem("Qual é o assunto dela?")
    harness.processar_mensagem(
        "Agora eu queria saber se existe outra nota parecida."
    )
    harness.processar_mensagem(
        "Agora deixa o Obsidian e procure o pyproject no projeto."
    )
    harness.processar_mensagem("Leia ele.")

    assert executor.calls == [
        ("obsidian_search_notes", {"query": "testes"}),
        ("obsidian_open_note", {"path": "Teste para Jarvis.md"}),
        ("obsidian_read_note", {"path": "Teste para Jarvis.md"}),
        ("obsidian_search_notes", {"query": "teste"}),
        ("list_files", {"path": "."}),
        ("read_file", {"path": "pyproject.toml"}),
    ]
    assert harness.context_router.state.active is ActiveContext.FILESYSTEM
    assert harness.recent_context.get("last_active_note") == (
        "Teste para Jarvis.md"
    )
    assert harness.recent_context.get("last_active_file") == "pyproject.toml"
    assert harness.recent_context.get("last_note_results") == (
        "Teste.md",
        "Teste para Jarvis.md",
    )
    assert harness.recent_context.get("last_file_results") == (
        "pyproject.toml",
    )


def test_explicit_obsidian_without_tools_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT", raising=False)
    llm = ScriptedLLM([tool_call("list_files", path="tests")])
    executor = RecordingExecutor()
    registry = ToolRegistry()
    registry.register(ListFilesTool(FileSystemGuard(tmp_path)))
    memory = ConversationMemory()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=executor,  # type: ignore[arg-type]
        memory=memory,
        config=JarvisConfig(settings_path=tmp_path / "missing.json"),
    )

    response = harness.processar_mensagem(
        "Procure minhas notas sobre teste."
    )

    assert "não está configurado" in response
    assert executor.calls == []
    assert llm.messages == []
    assert memory.get_history() == []
    assert harness.context_router.state.active is ActiveContext.NONE


def test_explicit_obsidian_phrase_without_tools_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT", raising=False)
    llm = ScriptedLLM([])
    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=RecordingExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        config=JarvisConfig(settings_path=tmp_path / "missing.json"),
    )

    response = harness.processar_mensagem(
        "Eu quero que você olhe no Obsidian."
    )

    assert "não está configurado" in response
    assert llm.messages == []
    assert harness.context_router.state.active is ActiveContext.NONE


def test_explicit_obsidian_phrase_sets_context_when_available(
    tmp_path: Path,
) -> None:
    harness, executor, _llm = build_context_harness(
        tmp_path, [LLMResponse("Estou olhando no Obsidian.")]
    )

    harness.processar_mensagem("Eu quero que você olhe no Obsidian.")

    assert harness.context_router.state.active is ActiveContext.OBSIDIAN
    assert executor.calls == []
    doctor = harness.processar_mensagem("/doctor")
    assert "Tools registered...... YES" in doctor


def test_obsidian_read_then_edit_followup_uses_append_note(
    tmp_path: Path,
) -> None:
    harness, executor, _llm = build_context_harness(
        tmp_path,
        [
            tool_call("list_files", path="tests"),
            LLMResponse("Encontrei."),
            tool_call("read_file", path="Teste para Jarvis"),
            LLMResponse("A nota foi lida."),
            tool_call(
                "edit_file", path="nela", content="Jarvis é lindo"
            ),
            LLMResponse("Conteúdo adicionado à nota."),
        ],
        obsidian_write_enabled=True,
        register_obsidian_write=True,
    )

    harness.processar_mensagem("Procure minhas notas sobre teste.")
    harness.processar_mensagem("Leia Teste para Jarvis.")
    assert harness.context_router.state.active is ActiveContext.OBSIDIAN
    assert harness.recent_context.get("last_active_note") == (
        "Teste para Jarvis.md"
    )

    harness.processar_mensagem("Adicione nela: Jarvis é lindo.")

    assert executor.calls == [
        ("obsidian_search_notes", {"query": "teste"}),
        ("obsidian_read_note", {"path": "Teste para Jarvis.md"}),
        (
            "obsidian_append_note",
            {"path": "Teste para Jarvis.md", "content": "Jarvis é lindo"},
        ),
    ]
    assert harness.context_router.state.active is ActiveContext.OBSIDIAN
    assert harness.recent_context.get("last_active_file") is None


def test_ele_edit_followup_uses_last_active_obsidian_note(
    tmp_path: Path,
) -> None:
    harness, executor, _llm = build_context_harness(
        tmp_path,
        [
            tool_call("edit_file", path="ele", content="Jarvis é lindo"),
            LLMResponse("Conteúdo adicionado à nota."),
        ],
        obsidian_write_enabled=True,
        register_obsidian_write=True,
    )
    harness.context_router.state.active = ActiveContext.OBSIDIAN
    harness.recent_context.set("last_active_note", "Teste para Jarvis.md")

    harness.processar_mensagem(
        "Você pode editar ele e colocar Jarvis é lindo dentro dele."
    )

    assert executor.calls == [
        (
            "obsidian_append_note",
            {"path": "Teste para Jarvis.md", "content": "Jarvis é lindo"},
        )
    ]


def test_obsidian_write_disabled_never_falls_back_to_filesystem(
    tmp_path: Path,
) -> None:
    harness, executor, llm = build_context_harness(
        tmp_path, [tool_call("edit_file", path="ela", content="x")]
    )
    harness.context_router.state.active = ActiveContext.OBSIDIAN
    harness.recent_context.set("last_active_note", "Teste para Jarvis.md")

    response = harness.processar_mensagem("Adicione isso nela.")

    assert "escrita no Obsidian" in response
    assert executor.calls == []
    assert llm.messages == []


def test_missing_obsidian_write_tool_never_falls_back_to_filesystem(
    tmp_path: Path,
) -> None:
    harness, executor, llm = build_context_harness(
        tmp_path,
        [tool_call("edit_file", path="ela", content="x")],
        obsidian_write_enabled=True,
    )
    harness.context_router.state.active = ActiveContext.OBSIDIAN
    harness.recent_context.set("last_active_note", "Teste para Jarvis.md")

    response = harness.processar_mensagem("Edite ela.")

    assert "indisponível" in response
    assert executor.calls == []
    assert llm.messages == []


def test_explicit_note_correction_restores_obsidian_without_file_search(
    tmp_path: Path,
) -> None:
    harness, executor, _llm = build_context_harness(
        tmp_path, [LLMResponse("Entendi, você está falando da nota.")]
    )
    harness.context_router.state.active = ActiveContext.FILESYSTEM
    harness.recent_context.set("last_active_note", "Teste para Jarvis.md")
    harness.recent_context.set("last_active_file", "pyproject.toml")

    harness.processar_mensagem(
        "Eu estou falando da nota que você acabou de me mostrar no Obsidian."
    )

    assert harness.context_router.state.active is ActiveContext.OBSIDIAN
    assert harness.recent_context.get("last_active_note") == (
        "Teste para Jarvis.md"
    )
    assert harness.recent_context.get("last_active_file") == "pyproject.toml"
    assert executor.calls == []


def test_successful_open_note_result_updates_active_note(
    tmp_path: Path,
) -> None:
    harness, executor, _llm = build_context_harness(
        tmp_path,
        [
            tool_call("obsidian_open_note", path="Teste para Jarvis.md"),
            LLMResponse("Nota aberta."),
        ],
    )

    harness.processar_mensagem(
        "Abra Teste para Jarvis.md no Obsidian."
    )

    assert executor.calls == [
        ("obsidian_open_note", {"path": "Teste para Jarvis.md"})
    ]
    assert harness.context_router.state.active is ActiveContext.OBSIDIAN
    assert harness.recent_context.get("last_active_note") == (
        "Teste para Jarvis.md"
    )
    assert harness.recent_context.get("last_active_file") is None


def test_successful_filesystem_read_result_updates_active_file(
    tmp_path: Path,
) -> None:
    harness, executor, _llm = build_context_harness(
        tmp_path,
        [
            tool_call("read_file", path="pyproject.toml"),
            LLMResponse("Arquivo lido."),
        ],
    )

    harness.processar_mensagem("Leia o arquivo pyproject no projeto.")

    assert executor.calls == [("read_file", {"path": "pyproject.toml"})]
    assert harness.context_router.state.active is ActiveContext.FILESYSTEM
    assert harness.recent_context.get("last_active_file") == "pyproject.toml"
    assert harness.recent_context.get("last_active_note") is None


def test_compound_open_and_read_executes_both_actions_before_final_response(
    tmp_path: Path,
) -> None:
    harness, executor, llm = build_context_harness(
        tmp_path,
        [
            tool_call(
                "obsidian_open_note",
                path="Teste para Jarvis.md",
            ),
            LLMResponse("Li a nota para você."),
        ],
    )

    response = harness.processar_mensagem(
        "Abra a nota Teste para Jarvis e leia ela."
    )

    assert response == "conteúdo da nota"
    assert executor.calls == [
        ("obsidian_open_note", {"path": "Teste para Jarvis.md"}),
        ("obsidian_read_note", {"path": "Teste para Jarvis.md"}),
    ]
    # Explicit open+read is now a deterministic fast compound path: no LLM
    # round-trip is required just to narrate a note the user asked to read.
    assert llm.messages == []
