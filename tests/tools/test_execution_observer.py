from pathlib import Path

from jarvis.security.audit import AuditLogger
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool
from jarvis.tools.executor import ToolExecutor


class SafeTool(Tool):
    name = "safe_observed"
    description = "test"
    risk = RiskLevel.READ_ONLY
    parameters = {"type": "object", "properties": {}, "required": []}

    def get_target(self, **_kwargs):  # type: ignore[no-untyped-def]
        return "x"

    def execute(self, **_kwargs):  # type: ignore[no-untyped-def]
        return "ok"


class FailingTool(SafeTool):
    name = "failing_observed"

    def execute(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | None]] = []

    def started(self, tool_name: str) -> None:
        self.events.append(("started", tool_name, None))

    def finished(self, tool_name, result):  # type: ignore[no-untyped-def]
        self.events.append(("finished", tool_name, result.status))


class BrokenObserver:
    def started(self, tool_name: str) -> None:
        raise RuntimeError("observer down")

    def finished(self, tool_name, result):  # type: ignore[no-untyped-def]
        raise RuntimeError("observer down")


def test_executor_observer_wraps_real_execution(tmp_path: Path) -> None:
    observer = RecordingObserver()
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
        observer=observer,
    )

    result = executor.execute_with_result(SafeTool())

    assert result.status == "success"
    assert observer.events == [
        ("started", "safe_observed", None),
        ("finished", "safe_observed", "success"),
    ]


def test_executor_observer_sees_runtime_error_after_execution_started(
    tmp_path: Path,
) -> None:
    observer = RecordingObserver()
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
        observer=observer,
    )

    result = executor.execute_with_result(FailingTool())

    assert result.status == "error"
    assert observer.events == [
        ("started", "failing_observed", None),
        ("finished", "failing_observed", "error"),
    ]


def test_broken_observer_never_breaks_tool_execution(tmp_path: Path) -> None:
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
        observer=BrokenObserver(),
    )

    assert executor.execute(SafeTool()) == "ok"
