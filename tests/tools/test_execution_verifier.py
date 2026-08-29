from pathlib import Path
from typing import Any

from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool, ToolPreparation
from jarvis.tools.executor import ToolExecutor


class ProbeTool(Tool):
    name = "desktop_focus_window"
    description = "probe"
    risk = RiskLevel.MEDIUM
    parameters = {"type": "object", "properties": {}}

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.executions = 0
        self.context = object()
        self.executed_context: object | None = None

    def get_target(self, **_kwargs: Any) -> str:
        return "janela:Obsidian"

    def execute(self, **_kwargs: Any) -> str:
        self.executions += 1
        if self.fail:
            raise RuntimeError("falha da Tool")
        return "efeito verificado pela Tool"

    def prepare(self, **_kwargs: Any) -> ToolPreparation:
        return ToolPreparation("janela:Obsidian", self.context)

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **_kwargs: Any,
    ) -> str:
        self.executed_context = preparation.context
        return self.execute()


class RecordingVerifier:
    def __init__(self, *, fail_before: bool = False) -> None:
        self.fail_before = fail_before
        self.before_calls: list[tuple[str, str, object | None]] = []
        self.after_calls: list[tuple[object, str, str, object | None]] = []
        self.cancelled: list[object] = []

    def before(
        self,
        tool_name: str,
        target: str,
        context: object | None = None,
    ) -> object:
        self.before_calls.append((tool_name, target, context))
        if self.fail_before:
            raise RuntimeError("Vision indisponível")
        return object()

    def after(
        self,
        token: object,
        tool_name: str,
        target: str,
        context: object | None = None,
    ) -> str:
        self.after_calls.append((token, tool_name, target, context))
        return "changed"

    def cancel(self, token: object) -> None:
        self.cancelled.append(token)


def executor(
    tmp_path: Path,
    verifier: RecordingVerifier,
    *,
    enabled: bool = True,
) -> ToolExecutor:
    return ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
        capability_policy=CapabilityPolicy(
            {Capability.DESKTOP_CONTROL: enabled}
        ),
        verifier=verifier,
    )


def test_visual_signal_is_additional_to_successful_tool_result(
    tmp_path: Path,
) -> None:
    verifier = RecordingVerifier()
    tool = ProbeTool()

    result = executor(tmp_path, verifier).execute_with_result(tool)

    assert result.status == "success"
    assert result.content == "efeito verificado pela Tool"
    assert result.visual_change == "changed"
    assert verifier.before_calls == [
        ("desktop_focus_window", "janela:Obsidian", tool.context)
    ]
    assert len(verifier.after_calls) == 1
    assert verifier.after_calls[0][3] is tool.context
    assert tool.executed_context is tool.context


def test_visual_failure_does_not_block_or_upgrade_tool_result(
    tmp_path: Path,
) -> None:
    verifier = RecordingVerifier(fail_before=True)

    result = executor(tmp_path, verifier).execute_with_result(ProbeTool())

    assert result.status == "success"
    assert result.visual_change is None


def test_verification_starts_only_after_authorization(tmp_path: Path) -> None:
    verifier = RecordingVerifier()
    tool = ProbeTool()

    result = executor(
        tmp_path,
        verifier,
        enabled=False,
    ).execute_with_result(tool)

    assert result.status == "security_blocked"
    assert verifier.before_calls == []
    assert tool.executions == 0


def test_tool_failure_cancels_visual_baseline(tmp_path: Path) -> None:
    verifier = RecordingVerifier()

    result = executor(tmp_path, verifier).execute_with_result(
        ProbeTool(fail=True)
    )

    assert result.status == "error"
    assert len(verifier.cancelled) == 1
    assert verifier.after_calls == []
