from pathlib import Path

import pytest

from jarvis.security.audit import AuditLogger


def test_audit_telemetry_reports_only_write_outcome(tmp_path: Path) -> None:
    outcomes: list[bool] = []
    logger = AuditLogger(tmp_path / "audit.log", outcomes.append)

    logger.log("secret_tool", "C:/secret/target.txt", True, "success")

    assert outcomes == [True]


def test_broken_telemetry_callback_does_not_block_audit(tmp_path: Path) -> None:
    def broken(_succeeded: bool) -> None:
        raise RuntimeError("telemetry unavailable")

    path = tmp_path / "audit.log"
    AuditLogger(path, broken).log("tool", "target", True, "success")

    assert path.exists()


def test_audit_write_failure_reports_unhealthy_and_still_raises(
    tmp_path: Path,
) -> None:
    outcomes: list[bool] = []
    logger = AuditLogger(tmp_path, outcomes.append)

    with pytest.raises(OSError):
        logger.log("tool", "target", True, "success")

    assert outcomes == [False]
