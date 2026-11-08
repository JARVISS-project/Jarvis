"""Exercise the real instance primitive with an isolated name and owned children."""
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from jarvis.runtime.instance import SingleInstanceGuard
from jarvis.runtime.lifecycle import RuntimeLifecycle, RuntimeState


CHILD = '''
import os
from pathlib import Path
import sys
from jarvis.runtime.instance import SingleInstanceGuard, InstanceAlreadyRunningError
guard = SingleInstanceGuard(Path(sys.argv[1]), mutex_name=sys.argv[2])
try:
    guard.acquire()
except InstanceAlreadyRunningError:
    raise SystemExit(23)
if sys.argv[3] == 'crash':
    os._exit(0)
guard.release()
'''


def child(metadata: Path, mutex: str, mode: str):
    return subprocess.run([sys.executable, '-c', CHILD, str(metadata), mutex, mode],
                          capture_output=True, text=True, timeout=10, check=False)


def test_start_second_instance_stop_and_restart_use_owned_namespace(tmp_path):
    metadata = tmp_path / 'instance.json'
    mutex = 'Local\\JARVIS.Sprint23BV.' + uuid4().hex
    guard = SingleInstanceGuard(metadata, mutex_name=mutex)
    lifecycle = RuntimeLifecycle()
    lifecycle.begin_startup()
    guard.acquire()
    lifecycle.add_cleanup(guard.release)
    lifecycle.mark_running()
    try:
        original = metadata.read_bytes()
        second = child(metadata, mutex, 'normal')
        assert second.returncode == 23, second.stderr
        assert metadata.read_bytes() == original
    finally:
        assert lifecycle.stop() == ()
    assert lifecycle.state is RuntimeState.STOPPED
    assert not metadata.exists()
    assert child(metadata, mutex, 'normal').returncode == 0
    assert not metadata.exists()


def test_recovery_after_owned_child_exit_preserves_foreign_metadata_until_acquire(tmp_path):
    metadata = tmp_path / 'instance.json'
    mutex = 'Local\\JARVIS.Sprint23BV.' + uuid4().hex
    crashed = child(metadata, mutex, 'crash')
    assert crashed.returncode == 0, crashed.stderr
    previous = json.loads(metadata.read_text())
    guard = SingleInstanceGuard(metadata, mutex_name=mutex)
    guard.acquire()
    try:
        assert json.loads(metadata.read_text())['instance_id'] != previous['instance_id']
    finally:
        guard.release()
    assert not metadata.exists()
