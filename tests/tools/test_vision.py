from pathlib import Path

import pytest

from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.vision import (
    VisionExtractTextTool,
    VisionGetUITreeTool,
    VisionObserveScreenChangeTool,
)
from jarvis.vision.models import CaptureRegion, CapturedFrame, TextResult, UITreeResult, UIElement
from jarvis.vision.service import VisionService


class FakeVisionBackend:
    def __init__(self) -> None:
        self.calls = 0
        self.text_results: list[TextResult] = []
        self.ui_tree_results: list[UITreeResult] = []

    def capture_screen(self) -> CapturedFrame:
        self.calls += 1
        return CapturedFrame(2, 1, "RGB", b"secret")

    def capture_region(self, region) -> CapturedFrame:
        self.calls += 1
        return CapturedFrame(region.width, region.height, "RGB", b"secret")

    def extract_text(self, region, language: str | None = None) -> TextResult:
        self.calls += 1
        result = TextResult(
            text="texto extraído",
            confidence=0.95,
            region=region,
            language=language,
        )
        self.text_results.append(result)
        return result

    def get_ui_tree(self, region=None) -> UITreeResult:
        self.calls += 1
        root = UIElement(
            name="root",
            control_type="Window",
            bounding_rect=region or CaptureRegion(0, 0, 100, 100),
        )
        result = UITreeResult(root=root, timestamp=0.0)
        self.ui_tree_results.append(result)
        return result


def build_tool(enabled: bool = True):  # type: ignore[no-untyped-def]
    backend = FakeVisionBackend()
    policy = CapabilityPolicy({Capability.VISION: enabled})
    return VisionObserveScreenChangeTool(VisionService(backend, policy)), backend


def build_extract_text_tool(enabled: bool = True):  # type: ignore[no-untyped-def]
    backend = FakeVisionBackend()
    policy = CapabilityPolicy({Capability.VISION: enabled})
    return VisionExtractTextTool(VisionService(backend, policy)), backend


def build_ui_tree_tool(enabled: bool = True):  # type: ignore[no-untyped-def]
    backend = FakeVisionBackend()
    policy = CapabilityPolicy({Capability.VISION: enabled})
    return VisionGetUITreeTool(VisionService(backend, policy)), backend


def test_vision_tool_exposes_no_image_or_input_arguments() -> None:
    tool, backend = build_tool()

    result = tool.execute()

    assert tool.risk is RiskLevel.MEDIUM
    assert tool.parameters["properties"] == {}
    assert "2x1" in result
    assert "Pixels não foram persistidos nem expostos" in result
    assert "secret" not in result
    assert backend.calls == 1


def test_extract_text_tool_requires_region_and_returns_text() -> None:
    tool, backend = build_extract_text_tool()

    result = tool.execute(left=10, top=20, right=110, bottom=120)

    assert "texto extraído" in result
    assert "confiança 95.0%" in result
    assert backend.calls == 1
    assert backend.text_results[0].language == "por+eng"


def test_extract_text_tool_custom_language() -> None:
    tool, backend = build_extract_text_tool()

    result = tool.execute(left=10, top=20, right=110, bottom=120, language="eng")

    assert backend.text_results[0].language == "eng"


def test_extract_text_tool_rejects_invalid_region() -> None:
    tool, backend = build_extract_text_tool()

    with pytest.raises(RuntimeError, match="dimensões inválidas"):
        tool.execute(left=10, top=20, right=10, bottom=30)

    assert backend.calls == 0


def test_extract_text_tool_rejects_oversized_region(tmp_path: Path) -> None:
    tool, backend = build_extract_text_tool()
    audit_path = tmp_path / "audit.log"
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(audit_path),
        capability_policy=CapabilityPolicy({Capability.VISION: True}),
    )

    result = executor.execute_with_result(tool, left=0, top=0, right=10000, bottom=10000)

    assert result.status == "error"
    assert "limite de pixels" in result.content
    assert backend.calls == 0


def test_ui_tree_tool_returns_formatted_tree() -> None:
    tool, backend = build_ui_tree_tool()

    result = tool.execute(left=10, top=20, right=110, bottom=120)

    assert "Window" in result
    assert "root" in result
    assert backend.calls == 1
    assert backend.ui_tree_results[0].root.bounding_rect == CaptureRegion(10, 20, 110, 120)


def test_ui_tree_tool_full_screen_when_no_region() -> None:
    tool, backend = build_ui_tree_tool()

    result = tool.execute()

    assert "Window" in result
    assert "root" in result
    assert backend.ui_tree_results[0].root.bounding_rect == CaptureRegion(0, 0, 100, 100)


def test_ui_tree_tool_rejects_invalid_region() -> None:
    tool, backend = build_ui_tree_tool()

    with pytest.raises(RuntimeError, match="dimensões inválidas"):
        tool.execute(left=10, top=20, right=10, bottom=30)

    assert backend.calls == 0


def test_ui_tree_tool_rejects_oversized_region(tmp_path: Path) -> None:
    tool, backend = build_ui_tree_tool()
    audit_path = tmp_path / "audit.log"
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(audit_path),
        capability_policy=CapabilityPolicy({Capability.VISION: True}),
    )

    result = executor.execute_with_result(tool, left=0, top=0, right=10000, bottom=10000)

    assert result.status == "error"
    assert "limite de pixels" in result.content
    assert backend.calls == 0


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


def test_extract_text_and_ui_tree_tools_blocked_when_disabled(tmp_path: Path) -> None:
    for tool_cls in (VisionExtractTextTool, VisionGetUITreeTool):
        backend = FakeVisionBackend()
        policy = CapabilityPolicy({Capability.VISION: False})
        tool = tool_cls(VisionService(backend, policy))
        executor = ToolExecutor(
            SafetyManager(),
            AuditLogger(tmp_path / "audit.log"),
            capability_policy=policy,
        )

        result = executor.execute_with_result(tool, left=0, top=0, right=10, bottom=10)

        assert result.status == "security_blocked"
        assert backend.calls == 0
