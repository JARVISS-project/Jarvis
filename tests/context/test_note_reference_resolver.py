from pathlib import Path

from jarvis.context.note_reference_resolver import (
    NoteMatchKind,
    NoteReferenceResolver,
)
from jarvis.context.recent import RecentContext
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.security.filesystem import FileSystemGuard


def resolver(tmp_path: Path) -> tuple[NoteReferenceResolver, RecentContext]:
    vault_path = tmp_path / "Vault"
    (vault_path / "Faculdade").mkdir(parents=True)
    (vault_path / "Projetos").mkdir()
    (vault_path / "Faculdade" / "Redes.md").write_text("x", encoding="utf-8")
    (vault_path / "Faculdade" / "VLSM.md").write_text("x", encoding="utf-8")
    (vault_path / "Projetos" / "Projeto Jarvis.md").write_text("x", encoding="utf-8")
    recent = RecentContext()
    vault = ObsidianVault(vault_path, FileSystemGuard(vault_path))
    return NoteReferenceResolver(vault, recent), recent


def test_resolves_recent_ordinal_without_authorization(tmp_path: Path) -> None:
    note_resolver, recent = resolver(tmp_path)
    recent.set(
        "last_note_results",
        ["Faculdade/Redes.md", "Faculdade/VLSM.md"],
    )
    match = note_resolver.resolve("leia a segunda")
    assert match.kind is NoteMatchKind.CONTEXT
    assert match.canonical_path == "Faculdade/VLSM.md"


def test_resolves_natural_note_name_and_preserves_literal_distinction(
    tmp_path: Path,
) -> None:
    note_resolver, _ = resolver(tmp_path)
    natural = note_resolver.resolve("projeto Jarvis ponto md")
    assert natural.canonical_path == "Projetos/Projeto Jarvis.md"
    assert natural.kind is not NoteMatchKind.EXACT_LITERAL
    literal = note_resolver.resolve("Faculdade/VLSM.md")
    assert literal.kind is NoteMatchKind.EXACT_LITERAL


def test_pronoun_prefers_last_active_note(tmp_path: Path) -> None:
    note_resolver, recent = resolver(tmp_path)
    recent.set("last_note", "Faculdade/Redes.md")
    recent.set("last_active_note", "Faculdade/VLSM.md")

    match = note_resolver.resolve("a nota que você acabou de ler")

    assert match.kind is NoteMatchKind.CONTEXT
    assert match.canonical_path == "Faculdade/VLSM.md"
