from pathlib import Path

import pytest

from jarvis.security.filesystem import FileSystemGuard
from jarvis.tools.delete_file import DeleteFileTool


def make_tool(base_directory: Path) -> DeleteFileTool:
    return DeleteFileTool(FileSystemGuard(base_directory))


def test_delete_file_removes_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "arquivo.txt"
    target.write_text("conteúdo", encoding="utf-8")

    result = make_tool(tmp_path).execute(path="arquivo.txt")

    assert result == "Arquivo excluído com sucesso."
    assert not target.exists()


def test_delete_file_rejects_missing_file(tmp_path: Path) -> None:
    result = make_tool(tmp_path).execute(path="ausente.txt")

    assert result == "Arquivo não encontrado."


def test_delete_file_rejects_directory(tmp_path: Path) -> None:
    (tmp_path / "pasta").mkdir()

    result = make_tool(tmp_path).execute(path="pasta")

    assert result == "O caminho informado não é um arquivo."
    assert (tmp_path / "pasta").is_dir()


def test_delete_file_blocks_sandbox_escape(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        make_tool(tmp_path).get_target(path="../fora.txt")


def test_delete_file_blocks_protected_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("segredo", encoding="utf-8")

    with pytest.raises(PermissionError):
        make_tool(tmp_path).execute(path=".env")


def test_delete_file_rejects_wildcards(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        make_tool(tmp_path).get_target(path="*.txt")
