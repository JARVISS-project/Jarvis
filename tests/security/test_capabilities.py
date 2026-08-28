from pathlib import Path

import pytest

from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.executor import ToolExecutor


def test_enabled_and_disabled_capabilities() -> None:
    policy = CapabilityPolicy({Capability.FILESYSTEM_READ: True})
    assert policy.is_enabled(Capability.FILESYSTEM_READ)
    with pytest.raises(PermissionError):
        policy.require(Capability.FILESYSTEM_WRITE)
    assert not policy.is_enabled(Capability.OBSIDIAN_WRITE)


def test_disabled_write_blocks_existing_tool(tmp_path: Path) -> None:
    policy = CapabilityPolicy({Capability.FILESYSTEM_WRITE: False})
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
        capability_policy=policy,
    )
    result = executor.execute(
        CreateFileTool(FileSystemGuard(tmp_path)),
        path="blocked.txt",
        content="x",
    )
    assert "capacidade desabilitada" in result.lower()
    assert not (tmp_path / "blocked.txt").exists()


def test_capability_does_not_bypass_security_policy() -> None:
    policy = CapabilityPolicy({Capability.FILESYSTEM_WRITE: True})
    security_policy = SafetyManager().policy
    assert policy.is_enabled(Capability.FILESYSTEM_WRITE)
    assert security_policy.is_allowed(RiskLevel.CRITICAL)
    assert security_policy.requires_confirmation(RiskLevel.CRITICAL)


def test_desktop_tools_are_mapped_to_separate_capabilities() -> None:
    from jarvis.security.capabilities import TOOL_CAPABILITIES

    assert TOOL_CAPABILITIES["desktop_list_windows"] is Capability.DESKTOP_READ
    assert (
        TOOL_CAPABILITIES["desktop_open_application"]
        is Capability.DESKTOP_CONTROL
    )
    assert (
        TOOL_CAPABILITIES["desktop_move_pointer_to_window"]
        is Capability.DESKTOP_CONTROL
    )
    assert (
        TOOL_CAPABILITIES["desktop_press_escape"]
        is Capability.DESKTOP_CONTROL
    )
    assert (
        TOOL_CAPABILITIES["desktop_close_window"]
        is Capability.DESKTOP_CONTROL
    )
    assert (
        TOOL_CAPABILITIES["vision_observe_screen_change"]
        is Capability.VISION
    )
