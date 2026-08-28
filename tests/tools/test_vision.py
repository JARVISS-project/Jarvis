from pathlib import Path

from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.vision import VisionObserveScreenChangeTool
from jarvis.vision.models import CapturedFrame
from jarvis.vision.service import VisionService


class FakeVisionBackend:
    def __init__(self) -> None:
        self.calls = 0

    def capture_screen(self) -> CapturedFrame:
        self.calls += 1
        return CapturedFrame(2, 1, "RGB", b"secret")


def build_tool(enabled: bool = True):  # type: ignore[no-untyped-def]
    backend = FakeVisionBackend()
    policy = CapabilityPolicy({Capability.VISION: enabled})
    return VisionObserveScreenChangeTool(VisionService(backend, policy)), backend


def test_vision_tool_exposes_no_image_or_input_arguments() -> None:
    tool, backend = build_tool()

    result = tool.execute()

    assert tool.risk is RiskLevel.MEDIUM
    assert tool.parameters["properties"] == {}
    assert "2x1" in result
    assert "Pixels não foram persistidos nem expostos" in result
    assert "secret" not in result
    assert backend.calls == 1


def test_tool_executor_audits_fixed_target_without_raw_frame(tmp_path: Path) -> None:
    tool, _backend = build_tool()
    audit_path = tmp_path / "audit.log"
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(audit_path),
        capability_policy=CapabilityPolicy({Capability.VISION: True}),
    )

    result = executor.execute_with_result(tool)

    assert result.status == "success"
    audit = audit_path.read_text(encoding="utf-8")
    assert "target=desktop:screen" in audit
    assert "secret" not in audit


def test_tool_executor_blocks_disabled_vision_before_capture(tmp_path: Path) -> None:
    tool, backend = build_tool()
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
        capability_policy=CapabilityPolicy({Capability.VISION: False}),
    )

    result = executor.execute_with_result(tool)

    assert result.status == "security_blocked"
    assert backend.calls == 0
