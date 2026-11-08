from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time

import pytest

from jarvis.speech.base import TranscriptionResult
from jarvis.speech.stt_process import (
    SttModelConfig, SttProcessClient, SttProcessError, SttProcessState,
)


from tests.speech.stt_spawn_fakes import SpawnModel, spawn_factory


@pytest.fixture(autouse=True)
def close_clients_even_when_assertions_fail(monkeypatch):
    clients = []
    original = SttProcessClient.__init__

    def tracked_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        clients.append(self)

    monkeypatch.setattr(SttProcessClient, "__init__", tracked_init)
    yield
    for stt in reversed(clients):
        stt.close()


def client(mode: str = "ok", **kwargs: object) -> SttProcessClient:
    values = {
        "timeout_seconds": .12, "startup_timeout_seconds": .8,
        "shutdown_timeout_seconds": .08, "max_restarts": 0,
    }
    values.update(kwargs)
    return SttProcessClient(
        SttModelConfig(mode), model_factory=spawn_factory, **values,
    )


def test_spawn_handshake_full_result_and_idempotent_shutdown() -> None:
    stt = client(); stt.warm_up()
    assert stt.state is SttProcessState.READY
    assert stt._context.get_start_method() == "spawn"
    result = stt.transcribe([1, 2, 3], 16000)
    assert result.text == "principal" and result.average_log_probability == -.1
    stt.close(); stt.close()
    assert stt.state is SttProcessState.STOPPED and not stt.worker_alive


@pytest.mark.parametrize(
    ("mode", "code"),
    [("hang", "timeout"), ("crash", "process_exit"), ("oom", "out_of_memory")],
)
def test_inference_failure_kills_worker_with_reason(mode: str, code: str) -> None:
    before = {child.pid for child in mp.active_children()}
    stt = client(mode); stt.warm_up(); started = time.monotonic()
    with pytest.raises(SttProcessError) as caught: stt.transcribe([1], 16000)
    assert caught.value.reason_code == code
    assert time.monotonic() - started < 1.0
    stt.close()
    assert not stt.worker_alive
    assert {child.pid for child in mp.active_children()} <= before


@pytest.mark.parametrize(
    ("mode", "code"),
    [("load_hang", "startup_timeout"), ("load_crash", "process_exit"),
     ("load_oom", "out_of_memory")],
)
def test_preload_failure_is_terminated(mode: str, code: str) -> None:
    stt = client(mode); started = time.monotonic()
    with pytest.raises(SttProcessError) as caught: stt.warm_up()
    assert caught.value.reason_code == code
    # The protocol deadline is 0.8 s; allow Windows spawn/CI teardown jitter
    # without weakening the configured timeout or relying on a long sleep.
    assert time.monotonic() - started < 2.0
    stt.close(); assert not stt.worker_alive


def test_shutdown_during_permanent_native_call_forces_process_death() -> None:
    stt = client("hang", timeout_seconds=10); stt.warm_up()
    errors: list[Exception] = []
    thread = threading.Thread(
        target=lambda: _capture(lambda: stt.transcribe([1], 16000), errors)
    )
    thread.start()
    deadline = time.monotonic() + 1
    while stt.state is not SttProcessState.BUSY:
        assert time.monotonic() < deadline; time.sleep(.005)
    started = time.monotonic(); stt.close(); thread.join(1)
    assert time.monotonic() - started < .8
    assert not thread.is_alive() and not stt.worker_alive


def _capture(operation: object, errors: list[Exception]) -> None:
    try: operation()  # type: ignore[operator]
    except Exception as error: errors.append(error)


def test_busy_rejects_second_task_and_never_applies_wrong_or_stale_result() -> None:
    stt = client("hang", timeout_seconds=.15); stt.warm_up()
    thread = threading.Thread(target=lambda: _capture(
        lambda: stt.transcribe([1], 16000), []
    )); thread.start()
    deadline = time.monotonic() + 1
    while stt.state is not SttProcessState.BUSY:
        assert time.monotonic() < deadline; time.sleep(.005)
    with pytest.raises(SttProcessError) as caught: stt.transcribe([2], 16000)
    assert caught.value.reason_code == "queue_full"
    stt._results.put({"v": 1, "kind": "result", "generation": stt.generation - 1,
                      "task_id": "old", "result": {"text": "old"}})
    stt._results.put({"v": 1, "kind": "result", "generation": stt.generation,
                      "task_id": "wrong", "result": {"text": "wrong"}})
    thread.join(1); stt.close(); assert not thread.is_alive()


def test_corrupt_ipc_restart_limit_and_recovery() -> None:
    stt = client("ok", max_restarts=1); stt.warm_up(); old = stt.generation
    stt._results.put(b"truncated")
    with pytest.raises(SttProcessError) as caught: stt.transcribe([1], 16000)
    assert caught.value.reason_code == "ipc_corrupt"
    assert stt.generation == old + 1 and stt.state is SttProcessState.READY
    assert stt.transcribe([1], 16000).text == "principal"
    stt.close()


def test_repeated_crash_stops_at_restart_limit() -> None:
    stt = client("crash", max_restarts=1); stt.warm_up()
    with pytest.raises(SttProcessError): stt.transcribe([1], 16000)
    assert stt.restart_count == 1 and stt.state is SttProcessState.READY
    with pytest.raises(SttProcessError): stt.transcribe([1], 16000)
    assert stt.restart_count == 1 and stt.state is SttProcessState.FAILED
    stt.close()


def test_shutdown_during_preload_cancels_and_leaves_no_worker() -> None:
    stt = client("load_hang", startup_timeout_seconds=10)
    errors: list[Exception] = []
    thread = threading.Thread(target=lambda: _capture(stt.warm_up, errors))
    thread.start()
    deadline = time.monotonic() + 1
    while stt.state is not SttProcessState.STARTING:
        assert time.monotonic() < deadline; time.sleep(.005)
    started = time.monotonic(); stt.close(); thread.join(1)
    assert time.monotonic() - started < .8
    assert not thread.is_alive() and not stt.worker_alive
    assert stt.state is SttProcessState.STOPPED


def test_no_pcm_credentials_or_tools_are_persisted() -> None:
    stt = client(); stt.warm_up(); stt.transcribe([32123], 16000); stt.close()
    assert not hasattr(stt, "pcm")
    names = set(stt.__dict__)
    assert not names.intersection({"tool", "tools", "credential", "token"})
