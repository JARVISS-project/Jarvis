from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from jarvis.__main__ import build_harness
from jarvis.config import JarvisConfig
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.security.audit import AuditLogger
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.obsidian import (
    ObsidianAppendNoteTool,
    ObsidianCreateNoteTool,
    ObsidianOpenNoteTool,
    ObsidianReadNoteTool,
    ObsidianSearchNotesTool,
)


@pytest.fixture
def vault(tmp_path: Path) -> ObsidianVault:
    root = tmp_path / "Meu Vault"
    root.mkdir()
    return ObsidianVault(root, FileSystemGuard(root))


def test_list_notes_ignores_obsidian_hidden_and_non_markdown(
    vault: ObsidianVault,
) -> None:
    (vault.vault_path / "Curso").mkdir()
    (vault.vault_path / "Curso" / "Nota.md").write_text("x", encoding="utf-8")
    (vault.vault_path / ".obsidian").mkdir()
    (vault.vault_path / ".obsidian" / "interno.md").write_text("x", encoding="utf-8")
    (vault.vault_path / ".oculto").mkdir()
    (vault.vault_path / ".oculto" / "nota.md").write_text("x", encoding="utf-8")
    (vault.vault_path / "arquivo.txt").write_text("x", encoding="utf-8")

    assert vault.list_notes() == ["Curso/Nota.md"]


def test_read_note_and_reject_invalid_paths(vault: ObsidianVault, tmp_path: Path) -> None:
    note = vault.vault_path / "Nota.md"
    note.write_text("conteúdo", encoding="utf-8")
    outside = tmp_path / "fora.md"
    outside.write_text("fora", encoding="utf-8")
    (vault.vault_path / ".obsidian").mkdir()
    (vault.vault_path / ".obsidian" / "config.md").write_text("x", encoding="utf-8")

    assert vault.read_note("Nota.md") == "conteúdo"
    for path in ("../fora.md", str(outside), ".obsidian/config.md", "Nota.txt"):
        with pytest.raises(PermissionError):
            vault.read_note(path)


def test_symlink_escape_is_blocked(vault: ObsidianVault, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("fora", encoding="utf-8")
    link = vault.vault_path / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks não disponíveis neste ambiente")
    with pytest.raises(PermissionError):
        vault.read_note("link.md")


def test_search_by_name_and_content_with_limited_snippet(vault: ObsidianVault) -> None:
    (vault.vault_path / "Dijkstra.md").write_text("algoritmo", encoding="utf-8")
    (vault.vault_path / "Redes.md").write_text(
        "A" * 300 + " roteamento " + "B" * 300, encoding="utf-8"
    )

    assert vault.search_notes("dijkstra")[0][0] == "Dijkstra.md"
    result = vault.search_notes("ROTEAMENTO")[0]
    assert result[0] == "Redes.md"
    assert "roteamento" in result[1]
    assert len(result[1]) <= vault.MAX_SNIPPET_LENGTH


def test_create_no_overwrite_and_append_preserves_content(vault: ObsidianVault) -> None:
    vault.create_note("Curso/Nova.md", "início")
    with pytest.raises(FileExistsError):
        vault.create_note("Curso/Nova.md", "mudou")
    vault.append_note("Curso/Nova.md", "\nfim")
    assert vault.read_note("Curso/Nova.md") == "início\nfim"


@pytest.mark.parametrize(
    "tool_factory,path",
    [(ObsidianCreateNoteTool, "nova.md"), (ObsidianAppendNoteTool, "existente.md")],
)
def test_writes_require_confirmation(
    tool_factory: type, path: str, vault: ObsidianVault,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (vault.vault_path / "existente.md").write_text("original", encoding="utf-8")
    executor = ToolExecutor(SafetyManager(), AuditLogger(tmp_path / "audit.log"))
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    result = executor.execute(tool_factory(vault), path=path, content="SEGREDO")
    assert "não autorizada" in result
    assert not (vault.vault_path / "nova.md").exists()
    assert (vault.vault_path / "existente.md").read_text(encoding="utf-8") == "original"
    assert "SEGREDO" not in (tmp_path / "audit.log").read_text(encoding="utf-8")


def test_open_note_validates_and_escapes_uri(vault: ObsidianVault) -> None:
    opened: list[str] = []
    vault._uri_opener = opened.append
    (vault.vault_path / "Pasta").mkdir()
    (vault.vault_path / "Pasta" / "Ação & teste.md").write_text("x", encoding="utf-8")
    tool = ObsidianOpenNoteTool(vault)
    tool.execute(path="Pasta/Ação & teste.md")
    parsed = urlparse(opened[0])
    assert parsed.scheme == "obsidian"
    assert parse_qs(parsed.query) == {
        "vault": ["Meu Vault"], "file": ["Pasta/Ação & teste.md"]
    }
    with pytest.raises(FileNotFoundError):
        tool.preflight(path="ausente.md")
    with pytest.raises(PermissionError):
        tool.preflight(path="https://example.test/nota.md")


def test_read_tool_size_limit(vault: ObsidianVault) -> None:
    (vault.vault_path / "grande.md").write_bytes(
        b"x" * (vault.MAX_NOTE_SIZE + 1)
    )
    with pytest.raises(ValueError):
        ObsidianReadNoteTool(vault).execute(path="grande.md")


def test_search_tool_does_not_return_complete_note(vault: ObsidianVault) -> None:
    content = "agulha " + "texto " * 100
    (vault.vault_path / "nota.md").write_text(content, encoding="utf-8")
    result = ObsidianSearchNotesTool(vault).execute(query="agulha")
    assert len(result) < len(content)


def test_disabled_and_enabled_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.__main__.OllamaLLM", lambda **_kwargs: object())
    monkeypatch.setattr("jarvis.__main__.SQLiteMemoryStore", lambda _path: object())
    disabled = build_harness(
        JarvisConfig(obsidian_enabled=False, obsidian_vault_path=None),
        ResourceManager(),
    )
    enabled = build_harness(
        JarvisConfig(obsidian_enabled=True, obsidian_vault_path=tmp_path),
        ResourceManager(),
    )
    assert disabled.tool_registry.get("obsidian_list_notes") is None
    assert enabled.tool_registry.get("obsidian_list_notes") is not None


def test_write_capability_controls_tool_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jarvis.__main__.OllamaLLM", lambda **_kwargs: object())
    monkeypatch.setattr("jarvis.__main__.SQLiteMemoryStore", lambda _path: object())
    read_only = build_harness(
        JarvisConfig(
            obsidian_enabled=True,
            obsidian_vault_path=tmp_path,
            obsidian_write_enabled=False,
        ),
        ResourceManager(),
    )
    writable = build_harness(
        JarvisConfig(
            obsidian_enabled=True,
            obsidian_vault_path=tmp_path,
            obsidian_write_enabled=True,
        ),
        ResourceManager(),
    )
    assert read_only.tool_registry.get("obsidian_create_note") is None
    assert read_only.tool_registry.get("obsidian_append_note") is None
    assert writable.tool_registry.get("obsidian_create_note") is not None
    assert writable.tool_registry.get("obsidian_append_note") is not None


def test_configured_limits_are_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.__main__.OllamaLLM", lambda **_kwargs: object())
    monkeypatch.setattr("jarvis.__main__.SQLiteMemoryStore", lambda _path: object())
    harness = build_harness(
        JarvisConfig(
            obsidian_enabled=True,
            obsidian_vault_path=tmp_path,
            obsidian_max_note_size_bytes=123,
            obsidian_search_max_results=4,
        ),
        ResourceManager(),
    )
    tool = harness.tool_registry.get("obsidian_search_notes")
    assert tool is not None
    assert tool.vault.max_note_size_bytes == 123
    assert tool.vault.search_max_results == 4


def test_recent_context_receives_ordered_note_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jarvis.__main__.OllamaLLM", lambda **_kwargs: object())
    monkeypatch.setattr("jarvis.__main__.SQLiteMemoryStore", lambda _path: object())
    harness = build_harness(
        JarvisConfig(obsidian_enabled=True, obsidian_vault_path=tmp_path),
        ResourceManager(),
    )
    harness._remember_operational_context(
        "obsidian_search_notes",
        {"query": "redes"},
        "- Faculdade/VLSM.md | score=3 | trecho\n"
        "- Faculdade/Redes.md | score=1 | trecho",
    )
    assert harness.recent_context.get("last_note_results") == (
        "Faculdade/VLSM.md",
        "Faculdade/Redes.md",
    )
