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
    assert policy.is_enabled(Capability.FILESYSTEM_WRITE)
    assert not SafetyManager().policy.is_allowed(RiskLevel.CRITICAL)
