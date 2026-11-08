from pathlib import Path

import pytest

from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.tools.create_file import CreateFileTool


def make_tool(base_directory: Path) -> CreateFileTool:
    return CreateFileTool(FileSystemGuard(base_directory))


def test_create_file_writes_utf8_content(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)

    result = tool.execute(path="novo.txt", content="Olá, Jarvis!")

    assert result == "Arquivo criado com sucesso."
    assert (tmp_path / "novo.txt").read_text(encoding="utf-8") == "Olá, Jarvis!"


def test_create_file_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "existente.txt"
    target.write_text("original", encoding="utf-8")
    tool = make_tool(tmp_path)

    result = tool.execute(path="existente.txt", content="alterado")

    assert result == "O arquivo já existe e não será sobrescrito."
    assert target.read_text(encoding="utf-8") == "original"


def test_create_file_does_not_create_missing_parent(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)

    result = tool.execute(path="ausente/novo.txt", content="conteúdo")

    assert result == "O diretório pai não existe."
    assert not (tmp_path / "ausente").exists()


def test_create_file_blocks_path_outside_sandbox(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)

    with pytest.raises(PermissionError):
        tool.get_target(path="../fora.txt", content="conteúdo")


def test_create_file_schema_and_risk() -> None:
    assert CreateFileTool.name == "create_file"
    assert CreateFileTool.risk is RiskLevel.EXTERNAL_EFFECT
    assert CreateFileTool.parameters["required"] == ["path", "content"]
    assert CreateFileTool.parameters["additionalProperties"] is False
