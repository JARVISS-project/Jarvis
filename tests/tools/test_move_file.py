from pathlib import Path

import pytest

from jarvis.security.filesystem import FileSystemGuard
from jarvis.tools.move_file import MoveFileTool


def make_tool(base_directory: Path) -> MoveFileTool:
    return MoveFileTool(FileSystemGuard(base_directory))


def test_move_file_moves_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "origem.txt"
    source.write_text("conteúdo", encoding="utf-8")

    result = make_tool(tmp_path).execute(
        source="origem.txt", destination="destino.txt"
    )

    assert result == "Arquivo movido com sucesso."
    assert not source.exists()
    assert (tmp_path / "destino.txt").read_text(encoding="utf-8") == "conteúdo"


def test_move_file_rejects_missing_source(tmp_path: Path) -> None:
    result = make_tool(tmp_path).execute(
        source="ausente.txt", destination="destino.txt"
    )

    assert result == "Arquivo de origem não encontrado."


def test_move_file_does_not_overwrite_destination(tmp_path: Path) -> None:
    (tmp_path / "origem.txt").write_text("origem", encoding="utf-8")
    destination = tmp_path / "destino.txt"
    destination.write_text("destino", encoding="utf-8")

    result = make_tool(tmp_path).execute(
        source="origem.txt", destination="destino.txt"
    )

    assert result == "O destino já existe e não será sobrescrito."
    assert destination.read_text(encoding="utf-8") == "destino"


@pytest.mark.parametrize(
    ("source", "destination"),
    [("../fora.txt", "destino.txt"), ("origem.txt", "../fora.txt")],
)
def test_move_file_blocks_sandbox_escape(
    tmp_path: Path, source: str, destination: str
) -> None:
    with pytest.raises(PermissionError):
        make_tool(tmp_path).get_target(
            source=source, destination=destination
        )


def test_move_file_rejects_directory(tmp_path: Path) -> None:
    (tmp_path / "pasta").mkdir()

    result = make_tool(tmp_path).execute(
        source="pasta", destination="destino"
    )

    assert result == "A origem informada não é um arquivo."
