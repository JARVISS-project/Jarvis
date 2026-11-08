from pathlib import Path

import pytest

from jarvis.context.file_reference_resolver import (
    FileMatchKind,
    FileReferenceResolver,
)
from jarvis.context.recent import RecentContext
from jarvis.security.filesystem import FileSystemGuard


@pytest.fixture
def resolver(tmp_path: Path) -> FileReferenceResolver:
    for name in (
        "teste_seguranca.txt",
        "README.md",
        "relatorio-final.md",
        "dados_2026.json",
    ):
        (tmp_path / name).write_text("x", encoding="utf-8")
    return FileReferenceResolver(FileSystemGuard(tmp_path))


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("teste underline segurança ponto txt", "teste_seguranca.txt"),
        ("teste segurança.txt", "teste_seguranca.txt"),
        ("teste-segurança.txt", "teste_seguranca.txt"),
        ("read me ponto md", "README.md"),
        ("relatório hífen final ponto md", "relatorio-final.md"),
        ("dados underline 2026 ponto json", "dados_2026.json"),
        ("teste de segurança", "teste_seguranca.txt"),
        ("teste de segurança ponto textista", "teste_seguranca.txt"),
        ("teste e segura", "teste_seguranca.txt"),
    ],
)
def test_spoken_names_resolve_only_to_existing_files(
    resolver: FileReferenceResolver,
    spoken: str,
    expected: str,
) -> None:
    match = resolver.resolve(spoken)
    assert match.path == expected
    assert match.kind in {FileMatchKind.NORMALIZED, FileMatchKind.FUZZY}


def test_missing_ambiguous_traversal_and_recent_context(tmp_path: Path) -> None:
    (tmp_path / "relatorio_um.txt").write_text("x", encoding="utf-8")
    (tmp_path / "relatorio-um.txt").write_text("x", encoding="utf-8")
    recent = RecentContext()
    resolver = FileReferenceResolver(FileSystemGuard(tmp_path), recent)
    assert resolver.resolve("totalmente ausente.bin").kind is FileMatchKind.NOT_FOUND
    assert resolver.resolve("relatorio um ponto txt").kind is FileMatchKind.AMBIGUOUS
    with pytest.raises(PermissionError):
        resolver.resolve("../fora.txt")
    recent.set("last_file", "relatorio_um.txt")
    contextual = resolver.resolve("ele")
    assert contextual.kind is FileMatchKind.CONTEXT
    assert contextual.path == "relatorio_um.txt"


def test_file_ordinal_uses_file_results_without_mixing_notes(
    tmp_path: Path,
) -> None:
    for name in ("primeiro.txt", "segundo.txt", "nota.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    recent = RecentContext()
    recent.set("last_file_results", ["primeiro.txt", "segundo.txt"])
    recent.set("last_note_results", ["nota.md"])
    resolver = FileReferenceResolver(FileSystemGuard(tmp_path), recent)

    match = resolver.resolve("abra a segunda")

    assert match.kind is FileMatchKind.CONTEXT
    assert match.path == "segundo.txt"


@pytest.mark.parametrize(
    "reference",
    [
        "ele",
        "esse arquivo",
        "aquele arquivo",
        "a que você abriu",
        "o arquivo que você acabou de abrir",
        "a anterior",
    ],
)
def test_natural_file_reference_uses_last_active_file(
    tmp_path: Path, reference: str
) -> None:
    (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
    recent = RecentContext()
    recent.set("last_active_file", "pyproject.toml")
    resolver = FileReferenceResolver(FileSystemGuard(tmp_path), recent)

    match = resolver.resolve(reference)

    assert match.kind is FileMatchKind.CONTEXT
    assert match.path == "pyproject.toml"


def test_pronoun_uses_only_unambiguous_single_file_result(
    tmp_path: Path,
) -> None:
    for name in ("pyproject.toml", "README.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    recent = RecentContext()
    recent.set("last_file_results", ["pyproject.toml"])
    resolver = FileReferenceResolver(FileSystemGuard(tmp_path), recent)

    assert resolver.resolve("ele").path == "pyproject.toml"

    recent.set("last_file_results", ["pyproject.toml", "README.md"])
    assert resolver.resolve("ele").kind is FileMatchKind.NOT_FOUND
