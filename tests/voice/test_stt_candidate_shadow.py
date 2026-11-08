from __future__ import annotations
import json, multiprocessing as mp, os, queue, time
from pathlib import Path
from queue import Empty
from threading import Event, Lock, Thread, current_thread, enumerate as threads
from jarvis.speech.base import TranscriptionResult
from jarvis.voice.capture_result import NoSpeechReason
import pytest
from jarvis.voice.stt_shadow import (
    CandidateModelConfig, CandidateState, SttCandidateShadow,
    candidate_process_main,
)

from tests.voice.stt_spawn_fakes import SpawnFakeStt, spawn_fake_factory

class DeterministicQueue(queue.Queue):
    def close(self)->None:pass
    def join_thread(self)->None:pass
    def cancel_join_thread(self)->None:pass

@pytest.fixture(autouse=True)
def close_shadows_even_when_assertions_fail(monkeypatch):
    instances = []
    original = SttCandidateShadow.__init__

    def tracked_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        instances.append(self)

    monkeypatch.setattr(SttCandidateShadow, "__init__", tracked_init)
    yield
    for instance in reversed(instances):
        instance.close()

class DeterministicProcess:
    def __init__(self,*,args,name,**_kwargs)->None:
        self.commands,self.results,self.generation,self.config,_factory=args
        self.name=name;self._alive=False;self._stop=Event();self._thread=None
    def start(self)->None:
        self._alive=True
        self._thread=Thread(target=self._run,name="fake-stt-worker",daemon=True);self._thread.start()
    def _run(self)->None:
        mode=self.config.model_name
        if mode=="load_crash":self._alive=False;return
        if mode=="load_hang":self._stop.wait();self._alive=False;return
        self.results.put({"v":1,"kind":"ready","generation":self.generation})
        while not self._stop.is_set():
            try:message=self.commands.get(timeout=.01)
            except Empty:continue
            if message.get("kind")=="shutdown":break
            if mode=="hang":continue
            if mode=="crash":self._alive=False;return
            code="out_of_memory" if mode=="oom" else None
            self.results.put({"v":1,"kind":"result","generation":self.generation,
                "task_id":message.get("task_id"),"result":{"text":"candidato",
                "language":"pt","duration_seconds":1.},"code":code})
        self._alive=False
    def is_alive(self)->bool:return self._alive
    def join(self,timeout=None)->None:
        if self._thread is not None:self._thread.join(timeout)
    def terminate(self)->None:
        self._stop.set();self._alive=False
        if self._thread is not None and self._thread is not current_thread():
            self._thread.join(.1)
    def kill(self)->None:self.terminate()
    def close(self)->None:pass

class DeterministicContext:
    def Queue(self,maxsize=0):return DeterministicQueue(maxsize)
    def Process(self,**kwargs):return DeterministicProcess(**kwargs)
    def get_start_method(self)->str:return "deterministic"

def _rows(path:Path)->list[dict]:return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
def _wait(shadow:SttCandidateShadow,state:CandidateState,timeout:float=3)->None:
    deadline=time.monotonic()+timeout
    while shadow.state is not state:
        assert time.monotonic()<deadline,(shadow.state,state);time.sleep(.005)
def _shadow(tmp_path:Path,mode:str="ok",**kwargs)->SttCandidateShadow:
    kwargs.setdefault("shutdown_timeout_seconds",.15)
    return SttCandidateShadow(model_name=mode,model_config=CandidateModelConfig(mode),
        model_factory=spawn_fake_factory,log_path=tmp_path/f"{mode}.jsonl",
        **kwargs)

def _deterministic_shadow(tmp_path:Path,mode:str="ok",**kwargs)->SttCandidateShadow:
    kwargs.setdefault("startup_timeout_seconds", .5)
    return _shadow(tmp_path,mode,context=DeterministicContext(),**kwargs)

def test_windows_spawn_handshake_pcm_correlation_and_shutdown(tmp_path:Path)->None:
    shadow=_shadow(tmp_path);_wait(shadow,CandidateState.READY,shadow.startup_timeout_seconds)
    assert shadow._context.get_start_method()=="spawn"
    pcm=[1,2,3];assert shadow.submit(pcm,16000,"principal",interaction_id="turn-1");pcm[0]=99
    _wait(shadow,CandidateState.READY);shadow.close();shadow.close()
    row=_rows(tmp_path/"ok.jsonl")[0]
    assert row["interaction_id"]=="turn-1" and row["candidate_text"]=="candidato"
    assert shadow.state is CandidateState.STOPPED and not shadow.worker_alive

def test_single_active_queue_is_nonblocking(tmp_path:Path)->None:
    shadow=_deterministic_shadow(tmp_path,"hang",timeout_seconds=.1,max_restarts=0);_wait(shadow,CandidateState.READY)
    started=time.monotonic();assert shadow.submit([1],16000,"one");assert not shadow.submit([2],16000,"two")
    assert time.monotonic()-started<.1;_wait(shadow,CandidateState.DEGRADED);shadow.close()
    assert {r["error_code"] for r in _rows(tmp_path/"hang.jsonl")}=={"queue_full","timeout"}

def test_submission_during_startup_is_not_ready_and_never_enqueued(tmp_path:Path)->None:
    shadow=_deterministic_shadow(tmp_path,"load_hang",startup_timeout_seconds=.2,max_restarts=0)
    assert shadow.state in {CandidateState.STARTING,CandidateState.DEGRADED}
    assert not shadow.submit([1],16000,"one",interaction_id="startup")
    _wait(shadow,CandidateState.DEGRADED);shadow.close()
    row=_rows(tmp_path/"load_hang.jsonl")[0]
    assert row["error_code"]=="not_ready" and row["dropped"] is True

def test_timeout_forces_process_and_restarts_with_new_generation(tmp_path:Path)->None:
    shadow=_deterministic_shadow(tmp_path,"hang",timeout_seconds=.08,max_restarts=1);_wait(shadow,CandidateState.READY)
    old=shadow.generation;assert shadow.submit([1],16000,"one");deadline=time.monotonic()+3
    while shadow.generation==old:assert time.monotonic()<deadline;time.sleep(.005)
    assert shadow.restart_count==1 and shadow.generation==old+1;shadow.close();assert not shadow.worker_alive

def test_crash_oom_and_restart_limit(tmp_path:Path)->None:
    oom=_deterministic_shadow(tmp_path,"oom");_wait(oom,CandidateState.READY);old=oom.generation
    assert oom.submit([1],16000,"secret");deadline=time.monotonic()+3
    while oom.generation==old:assert time.monotonic()<deadline;time.sleep(.005)
    oom.close();assert _rows(tmp_path/"oom.jsonl")[0]["error_code"]=="out_of_memory"
    crash=_deterministic_shadow(tmp_path,"crash",max_restarts=1);_wait(crash,CandidateState.READY);assert crash.submit([1],16000,"x")
    deadline=time.monotonic()+3
    while crash.restart_count<1:assert time.monotonic()<deadline;time.sleep(.005)
    _wait(crash,CandidateState.READY);assert crash.submit([1],16000,"y");_wait(crash,CandidateState.DEGRADED)
    assert crash.restart_count==1;crash.close()

def test_load_timeout_shutdown_during_load_and_no_orphan(tmp_path:Path)->None:
    before={p.pid for p in mp.active_children()};shadow=_deterministic_shadow(tmp_path,"load_hang",max_restarts=0)
    _wait(shadow,CandidateState.DEGRADED);shadow.close();assert {p.pid for p in mp.active_children()}<=before
    other=_deterministic_shadow(tmp_path,"load_hang",max_restarts=0);started=time.monotonic();other.close()
    assert time.monotonic()-started<1 and not other.worker_alive

def test_shutdown_during_inference_forces_cleanup(tmp_path:Path)->None:
    shadow=_deterministic_shadow(tmp_path,"hang",timeout_seconds=10,max_restarts=0)
    _wait(shadow,CandidateState.READY);assert shadow.submit([1],16000,"one")
    _wait(shadow,CandidateState.BUSY);started=time.monotonic();shadow.close()
    assert time.monotonic()-started<1 and not shadow.worker_alive

def test_child_protocol_has_no_tool_or_credential_input()->None:
    names=set(candidate_process_main.__code__.co_varnames)
    assert not names.intersection({"tool","tools","credential","credentials","token"})

def test_stale_wrong_and_corrupt_ipc(tmp_path:Path)->None:
    shadow=_deterministic_shadow(tmp_path);_wait(shadow,CandidateState.READY)
    shadow._handle({"v":1,"kind":"result","generation":shadow.generation-1,"task_id":"old","text":"old"})
    shadow._handle({"v":1,"kind":"result","generation":shadow.generation,"task_id":"wrong","text":"wrong"})
    assert shadow.state is CandidateState.READY
    shadow._results.put({"bad":True});deadline=time.monotonic()+3
    while shadow.generation==1:assert time.monotonic()<deadline;time.sleep(.005)
    shadow.close()

def test_telemetry_sanitized_bounded_and_never_persists_pcm(tmp_path:Path)->None:
    path=tmp_path/"safe.jsonl";shadow=SttCandidateShadow(model_name="ok",model_config=CandidateModelConfig("ok"),
        model_factory=spawn_fake_factory,context=DeterministicContext(),log_path=path,max_bytes=350,backup_count=1);_wait(shadow,CandidateState.READY)
    for index in range(5):assert shadow.submit([32123,index],16000,"senha=minhasenha");_wait(shadow,CandidateState.READY)
    shadow.close();content="".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("safe.jsonl*"))
    assert "minhasenha" not in content and "32123" not in content and len(list(tmp_path.glob("safe.jsonl*")))<=2

def test_no_speech_reason_codes_use_observed_capture_facts()->None:
    from jarvis.voice.pipeline import VoicePipeline
    class Recorder:
        last_frames_received=0;last_speech_samples=0;last_average_speech_energy=0.;last_max_frame_energy=0.
    recorder=Recorder();assert VoicePipeline._no_speech_reason(recorder,None)==NoSpeechReason.NO_FRAMES.value
    recorder.last_frames_received=4;assert VoicePipeline._no_speech_reason(recorder,"pre_speech_timeout")==NoSpeechReason.SILENT_FRAMES.value
    recorder.last_speech_samples=20;recorder.last_max_frame_energy=100.
    assert VoicePipeline._no_speech_reason(recorder,"pre_speech_timeout")==NoSpeechReason.PRE_SPEECH_TIMEOUT.value
    assert VoicePipeline._no_speech_reason(recorder,"speech_rejected")==NoSpeechReason.SPEECH_TOO_SHORT.value
    assert VoicePipeline._no_speech_reason(recorder,"ptt_release")==NoSpeechReason.EARLY_TERMINATION.value

def test_ready_message_wins_at_startup_timeout_boundary()->None:
    class AliveProcess:
        def is_alive(self)->bool:return True
    class ReadyQueue:
        def __init__(self)->None:self.used=False
        def get_nowait(self):
            if self.used:raise Empty
            self.used=True
            return {"v":1,"kind":"ready","generation":1}
    shadow=SttCandidateShadow.__new__(SttCandidateShadow)
    shadow._lock=Lock();shadow._process=AliveProcess();shadow._results=ReadyQueue()
    shadow._state=CandidateState.STARTING;shadow._active=None;shadow._generation=1
    shadow._clock=lambda:2.;shadow._started_at=0.;shadow.startup_timeout_seconds=1.
    shadow.timeout_seconds=10.
    shadow._poll()
    assert shadow.state is CandidateState.READY and shadow._results.used

def test_sequential_and_concurrent_instances_leave_no_children(tmp_path:Path)->None:
    before={process.pid for process in mp.active_children()}
    first=_deterministic_shadow(tmp_path/"sequential-1");_wait(first,CandidateState.READY);first.close()
    second=_deterministic_shadow(tmp_path/"sequential-2");_wait(second,CandidateState.READY);second.close()
    left=_deterministic_shadow(tmp_path/"concurrent-left");right=_deterministic_shadow(tmp_path/"concurrent-right")
    _wait(left,CandidateState.READY);_wait(right,CandidateState.READY)
    left.close();right.close()
    assert {process.pid for process in mp.active_children()}<=before
    assert not any(thread.name in {"fake-stt-worker","stt-candidate-supervisor"}
                   for thread in threads())

def test_failures_do_not_contaminate_next_instance(tmp_path:Path)->None:
    for mode,timeout in (("oom",20.),("crash",20.),("hang",.08)):
        failed=_deterministic_shadow(tmp_path/mode,mode,timeout_seconds=timeout,max_restarts=0)
        _wait(failed,CandidateState.READY);assert failed.submit([1],16000,mode)
        _wait(failed,CandidateState.DEGRADED);failed.close()
        clean=_deterministic_shadow(tmp_path/f"after-{mode}")
        _wait(clean,CandidateState.READY);clean.close()
    assert not any(process.name=="stt-candidate-shadow" for process in mp.active_children())

def test_shutdown_abandons_queue_without_waiting_for_absent_consumer(tmp_path):
    class NoConsumerQueue(DeterministicQueue):
        def __init__(self, maxsize=0):
            super().__init__(maxsize)
            self.closed = self.cancelled = False
        def close(self):
            self.closed = True
        def join_thread(self):
            raise AssertionError("unbounded feeder join with no consumer")
        def cancel_join_thread(self):
            self.cancelled = True

    class NoConsumerContext(DeterministicContext):
        def __init__(self):
            self.queues = []
        def Queue(self, maxsize=0):
            channel = NoConsumerQueue(maxsize)
            self.queues.append(channel)
            return channel

    context = NoConsumerContext()
    shadow = _shadow(tmp_path, context=context)
    _wait(shadow, CandidateState.READY)
    shadow.close()
    assert all(channel.closed and channel.cancelled for channel in context.queues)


def test_close_after_exhausted_startup_failure_is_stopped(tmp_path):
    shadow = _deterministic_shadow(tmp_path, 'load_crash', max_restarts=0)
    _wait(shadow, CandidateState.DEGRADED)
    shadow.close()
    assert shadow.state is CandidateState.STOPPED
    assert not shadow.worker_alive
    assert not shadow._thread.is_alive()


@pytest.mark.parametrize('now,expected', [(0.999, []), (1.0, []),
                                         (1.001, ['startup_timeout'])])
def test_startup_deadline_remains_bounded_without_ready(now, expected):
    class AliveProcess:
        def is_alive(self):
            return True

    shadow = SttCandidateShadow.__new__(SttCandidateShadow)
    shadow._lock = Lock()
    shadow._process = AliveProcess()
    shadow._results = DeterministicQueue()
    shadow._state = CandidateState.STARTING
    shadow._active = None
    shadow._generation = 1
    shadow._clock = lambda: now
    shadow._started_at = 0.
    shadow.startup_timeout_seconds = 1.
    shadow.timeout_seconds = 10.
    failures = []
    shadow._fail_generation = failures.append
    shadow._poll()
    assert failures == expected
