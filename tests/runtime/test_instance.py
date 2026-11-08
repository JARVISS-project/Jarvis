import json
from pathlib import Path

import pytest

from jarvis.runtime.instance import (
    InstanceAlreadyRunningError,
    SingleInstanceGuard,
)


def test_single_instance_rejects_live_owner(tmp_path: Path) -> None:
    metadata = tmp_path / "instance.json"
    first = SingleInstanceGuard(metadata, platform="linux", pid=101)
    second = SingleInstanceGuard(
        metadata,
        platform="linux",
        pid=202,
        is_process_running=lambda pid: pid == 101,
    )
    first.acquire()
    try:
        with pytest.raises(InstanceAlreadyRunningError):
            second.acquire()
    finally:
        first.release()


def test_single_instance_recovers_stale_lock(tmp_path: Path) -> None:
    metadata = tmp_path / "instance.json"
    metadata.write_text(json.dumps({"pid": 77}), encoding="utf-8")
    metadata.with_suffix(".lock").write_text("", encoding="utf-8")
    guard = SingleInstanceGuard(
        metadata,
        platform="linux",
        pid=303,
        is_process_running=lambda _pid: False,
    )

    guard.acquire()
    assert json.loads(metadata.read_text(encoding="utf-8"))["pid"] == 303
    guard.release()
    assert not metadata.exists()
    assert not metadata.with_suffix(".lock").exists()


def test_single_instance_release_does_not_remove_new_owner_metadata(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "instance.json"
    guard = SingleInstanceGuard(metadata, platform="linux")
    guard.acquire()
    metadata.write_text(json.dumps({"instance_id": "new-owner"}), encoding="utf-8")

    guard.release()

    assert metadata.exists()
