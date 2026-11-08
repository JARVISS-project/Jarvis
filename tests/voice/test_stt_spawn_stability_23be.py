"""Real Windows spawn uses the production startup contract, not policy clocks."""
import multiprocessing as mp
from threading import enumerate as threads
import time

import pytest

from tests.voice.test_stt_candidate_shadow import _shadow, _wait, CandidateState
from tests.voice.stt_spawn_fakes import spawn_cpu_load


@pytest.mark.parametrize('round_number', range(3))
@pytest.mark.parametrize('concurrent', [False, True])
@pytest.mark.parametrize('load_workers', [0, 2])
def test_real_spawn_isolation_and_bounded_cleanup(tmp_path, round_number,
                                                 concurrent, load_workers):
    before = {child.pid for child in mp.active_children()}
    thread_before = set(threads())
    context = mp.get_context('spawn')
    stop = context.Event()
    loaders = [context.Process(target=spawn_cpu_load, args=(stop,))
               for _ in range(load_workers)]
    shadows = []
    channels = []
    processes = []
    try:
        for loader in loaders:
            loader.start()
        for index in range(2):
            shadow = _shadow(tmp_path / str(index))
            shadows.append(shadow)
            if not concurrent:
                _wait(shadow, CandidateState.READY, shadow.startup_timeout_seconds)
                channels.extend((shadow._commands, shadow._results))
                processes.append(shadow._process)
                shadow.close()
        if concurrent:
            for shadow in shadows:
                _wait(shadow, CandidateState.READY, shadow.startup_timeout_seconds)
                channels.extend((shadow._commands, shadow._results))
                processes.append(shadow._process)
            assert shadows[0]._commands is not shadows[1]._commands
            assert shadows[0]._results is not shadows[1]._results
            assert shadows[0]._process.pid != shadows[1]._process.pid
    finally:
        for shadow in reversed(shadows):
            shadow.close()
        stop.set()
        for loader in loaders:
            loader.join(10)
            assert not loader.is_alive()
            loader.close()
    assert all(shadow.state is CandidateState.STOPPED for shadow in shadows)
    assert all(not shadow.worker_alive for shadow in shadows)
    assert all(process._closed for process in processes)
    for channel in channels:
        assert channel._closed
        feeder = channel._thread
        if feeder is not None:
            feeder.join(3)
            assert not feeder.is_alive()
    assert {child.pid for child in mp.active_children()} <= before
    assert not any(thread not in thread_before and thread.name == 'stt-candidate-supervisor'
                   for thread in threads())


@pytest.mark.parametrize('mode', ['load_hang', 'hang'])
def test_real_spawn_cancel_startup_and_inference(tmp_path, mode):
    before = {child.pid for child in mp.active_children()}
    shadow = _shadow(tmp_path, mode, max_restarts=0)
    try:
        if mode == 'hang':
            _wait(shadow, CandidateState.READY, shadow.startup_timeout_seconds)
            assert shadow.submit([1], 16000, 'synthetic')
            _wait(shadow, CandidateState.BUSY)
        else:
            _wait(shadow, CandidateState.STARTING)
        started = time.monotonic()
        shadow.close()
        assert time.monotonic() - started < shadow.shutdown_timeout_seconds * 3 + 1
        assert shadow.state is CandidateState.STOPPED
        assert not shadow.worker_alive
    finally:
        shadow.close()
    assert {child.pid for child in mp.active_children()} <= before
