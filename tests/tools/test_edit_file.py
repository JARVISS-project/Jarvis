from pathlib import Path

import pytest

from jarvis.security.filesystem import FileSystemGuard
from jarvis.tools.edit_file import EditFileTool


def make_tool(base_directory: Path) -> EditFileTool:
    return EditFileTool(FileSystemGuard(base_directory))


def test_edit_file_updates_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "arquivo.txt"
    target.write_text("antes", encoding="utf-8")

    result = make_tool(tmp_path).execute(path="arquivo.txt", content="depois")

    assert result == "Arquivo editado com sucesso."
    assert target.read_text(encoding="utf-8") == "depois"


def test_edit_file_rejects_missing_file(tmp_path: Path) -> None:
    result = make_tool(tmp_path).execute(path="ausente.txt", content="texto")

    assert result == "Arquivo não encontrado."
    assert not (tmp_path / "ausente.txt").exists()


def test_edit_file_blocks_sandbox_escape(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        make_tool(tmp_path).get_target(path="../fora.txt", content="texto")


def test_edit_file_blocks_protected_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("segredo", encoding="utf-8")

    with pytest.raises(PermissionError):
        make_tool(tmp_path).execute(path=".env", content="alterado")
