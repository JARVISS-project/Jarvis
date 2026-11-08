from pathlib import Path
from typing import Any

import pytest

from jarvis.security.audit import AuditLogger
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.edit_file import EditFileTool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.list_files import ListFilesTool
from jarvis.tools.move_file import MoveFileTool
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.registry import ToolRegistry


@pytest.fixture
def security_layer(tmp_path: Path) -> tuple[
    FileSystemGuard, ToolExecutor, Path
]:
    audit_file = tmp_path / "audit.log"
    executor = ToolExecutor(
        safety_manager=SafetyManager(),
        audit_logger=AuditLogger(audit_file),
    )
    return FileSystemGuard(tmp_path), executor, audit_file


def authorize(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)


def test_create_authorized_creates_file(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(
        CreateFileTool(filesystem), path="created.txt", content="text"
    )

    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "text"


def test_create_external_effect_rejects_on_n(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "n")

    executor.execute(
        CreateFileTool(filesystem), path="denied.txt", content="text"
    )

    assert not (tmp_path / "denied.txt").exists()


def test_create_does_not_overwrite_existing_file(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("original", encoding="utf-8")
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(
        CreateFileTool(filesystem), path="existing.txt", content="changed"
    )

    assert target.read_text(encoding="utf-8") == "original"


def test_create_sandbox_escape_is_blocked(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
) -> None:
    filesystem, executor, _ = security_layer

    result = executor.execute(
        CreateFileTool(filesystem), path="../outside.txt", content="text"
    )

    assert "seguran" in result.lower()
    assert not (tmp_path.parent / "outside.txt").exists()


def test_read_allowed_file(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
) -> None:
    (tmp_path / "allowed.txt").write_text("allowed content", encoding="utf-8")
    filesystem, executor, _ = security_layer

    result = executor.execute(ReadFileTool(filesystem), path="allowed.txt")

    assert result == "allowed content"


@pytest.mark.parametrize("path", [".env", "../outside.txt"])
def test_read_protected_or_outside_file_is_blocked(
    path: str,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
) -> None:
    filesystem, executor, _ = security_layer

    result = executor.execute(ReadFileTool(filesystem), path=path)

    assert "seguran" in result.lower()


def test_edit_authorized_updates_file(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "editable.txt"
    target.write_text("before", encoding="utf-8")
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(EditFileTool(filesystem), path="editable.txt", content="after")

    assert target.read_text(encoding="utf-8") == "after"


def test_edit_external_effect_rejects_on_n(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "editable.txt"
    target.write_text("before", encoding="utf-8")
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "n")

    executor.execute(EditFileTool(filesystem), path="editable.txt", content="after")

    assert target.read_text(encoding="utf-8") == "before"


def test_edit_missing_file_is_not_created(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(EditFileTool(filesystem), path="missing.txt", content="text")

    assert not (tmp_path / "missing.txt").exists()


def test_move_authorized_moves_file(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(
        MoveFileTool(filesystem), source="source.txt", destination="destination.txt"
    )

    assert not source.exists()
    assert (tmp_path / "destination.txt").read_text(encoding="utf-8") == "content"


def test_move_external_effect_requests_confirmation(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(
        MoveFileTool(filesystem), source="source.txt", destination="destination.txt"
    )

    assert not source.exists()
    assert (tmp_path / "destination.txt").read_text(encoding="utf-8") == "content"


def test_move_does_not_overwrite_destination(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("destination", encoding="utf-8")
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(
        MoveFileTool(filesystem), source="source.txt", destination="destination.txt"
    )

    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "destination"


def test_move_does_not_move_directory(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "directory"
    source.mkdir()
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(
        MoveFileTool(filesystem), source="directory", destination="moved"
    )

    assert source.is_dir()
    assert not (tmp_path / "moved").exists()


def test_delete_destructive_risk_requests_confirmation(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "delete.txt"
    target.write_text("content", encoding="utf-8")
    filesystem, executor, _ = security_layer
    prompts: list[str] = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or "n"
    )

    executor.execute(DeleteFileTool(filesystem), path="delete.txt")

    assert len(prompts) == 1
    assert target.exists()


def test_delete_destructive_rejects_on_n(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "delete.txt"
    target.write_text("content", encoding="utf-8")
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "n")

    executor.execute(DeleteFileTool(filesystem), path="delete.txt")

    assert target.exists()


def test_delete_does_not_remove_directory(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "directory"
    target.mkdir()
    filesystem, executor, _ = security_layer
    authorize(monkeypatch, "s")

    executor.execute(DeleteFileTool(filesystem), path="directory")

    assert target.is_dir()


def test_delete_sandbox_escape_is_blocked(
    tmp_path: Path,
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
) -> None:
    filesystem, executor, _ = security_layer

    result = executor.execute(DeleteFileTool(filesystem), path="../outside.txt")

    assert "seguran" in result.lower()
    assert not (tmp_path.parent / "outside.txt").exists()


def test_audit_records_allowed_denied_and_security_blocked_actions(
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem, executor, audit_file = security_layer
    authorize(monkeypatch, "s")
    executor.execute(CreateFileTool(filesystem), path="allowed.txt", content="safe")
    authorize(monkeypatch, "n")

    class CriticalTool(Tool):
        name = "critical_test"
        description = "Tool crítica usada para validar negação e audit."
        risk = RiskLevel.DESTRUCTIVE
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "required": [],
        }

        def get_target(self, **kwargs: Any) -> str:
            return "critical:target"

        def execute(self, **kwargs: Any) -> str:
            return "executado"

    executor.execute(CriticalTool())
    executor.execute(ReadFileTool(filesystem), path="../outside.txt")

    audit = audit_file.read_text(encoding="utf-8")
    assert "tool=create_file" in audit
    assert "authorized=True status=success" in audit
    assert "authorized=False status=denied" in audit
    assert "authorized=False status=security_blocked" in audit


def test_audit_does_not_include_file_content(
    security_layer: tuple[FileSystemGuard, ToolExecutor, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem, executor, audit_file = security_layer
    secret_content = "CONTENT_MUST_NOT_APPEAR_IN_AUDIT"
    authorize(monkeypatch, "s")

    executor.execute(
        CreateFileTool(filesystem), path="content.txt", content=secret_content
    )
    executor.execute(ReadFileTool(filesystem), path="content.txt")

    assert secret_content not in audit_file.read_text(encoding="utf-8")


def test_registry_finds_tools_and_rejects_duplicates(tmp_path: Path) -> None:
    filesystem = FileSystemGuard(tmp_path)
    tools = [
        CreateFileTool(filesystem),
        ReadFileTool(filesystem),
        EditFileTool(filesystem),
        MoveFileTool(filesystem),
        DeleteFileTool(filesystem),
        ListFilesTool(filesystem),
    ]
    registry = ToolRegistry()

    for tool in tools:
        registry.register(tool)

    for tool in tools:
        assert registry.get(tool.name) is tool
    assert registry.get("missing_tool") is None

    with pytest.raises(ValueError):
        registry.register(CreateFileTool(filesystem))
