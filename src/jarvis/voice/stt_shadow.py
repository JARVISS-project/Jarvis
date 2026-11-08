"""Terminating process isolation for non-authoritative candidate STT."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json, logging, multiprocessing as mp, time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Full
from threading import Event, Lock, Thread
from typing import Any, Callable
from uuid import uuid4

from jarvis.speech.stt_process import (
    PROTOCOL_VERSION,
    SttModelConfig,
    SttProcessState,
    default_model_factory,
    stt_process_main,
)
from jarvis.voice.semantic_shadow import parse_text, sanitize_text

DEFAULT_LOG_PATH = Path("data/voice/stt_candidate_shadow.jsonl")
CandidateState = SttProcessState
CandidateModelConfig = SttModelConfig

@dataclass(frozen=True)
class CandidateJob:
    interaction_id: str; pcm: tuple[int, ...]; sample_rate: int; primary_text: str

_default_model_factory = default_model_factory
candidate_process_main = stt_process_main

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record.msg,ensure_ascii=False,separators=(",",":"))

class SttCandidateShadow:
    """Supervise one disposable candidate process; output is telemetry only."""
    def __init__(self, factory: Callable[[],Any]|None=None, *, model_name: str,
                 model_config: CandidateModelConfig|None=None,
                 model_factory: Callable[[CandidateModelConfig],Any]=_default_model_factory,
                 timeout_seconds: float=20., startup_timeout_seconds: float=30.,
                 shutdown_timeout_seconds: float=3., max_restarts: int=2,
                 queue_size: int=1, log_path: Path=DEFAULT_LOG_PATH,
                 max_bytes: int=250_000, backup_count: int=2,
                 clock: Callable[[],float]=time.monotonic, context: Any|None=None) -> None:
        if factory is not None: raise TypeError("use a spawn-safe model_factory")
        if queue_size!=1 or min(timeout_seconds,startup_timeout_seconds,shutdown_timeout_seconds)<=0 or max_restarts<0:
            raise ValueError("invalid candidate process limits")
        self.model_name=model_name; self.timeout_seconds=timeout_seconds
        self.startup_timeout_seconds=startup_timeout_seconds
        self.shutdown_timeout_seconds=shutdown_timeout_seconds; self.max_restarts=max_restarts
        self._config=model_config or CandidateModelConfig(model_name); self._model_factory=model_factory
        self._clock=clock; self._context=context or mp.get_context("spawn")
        self._closed=Event(); self._wake=Event(); self._lock=Lock()
        self._state=CandidateState.STOPPED; self._generation=0; self._restarts=0
        self._process=self._commands=self._results=None; self._active=None
        log_path.parent.mkdir(parents=True,exist_ok=True)
        self._logger=logging.getLogger(f"jarvis.voice.stt_shadow.{id(self)}")
        self._logger.setLevel(logging.INFO); self._logger.propagate=False
        handler=RotatingFileHandler(log_path,maxBytes=max_bytes,backupCount=backup_count,encoding="utf-8")
        handler.setFormatter(_JsonFormatter()); self._logger.addHandler(handler)
        self._thread=Thread(target=self._supervise,name="stt-candidate-supervisor",daemon=True); self._thread.start()

    @property
    def state(self)->CandidateState:
        with self._lock: return self._state
    @property
    def worker_alive(self)->bool:
        p=self._process; return bool(p is not None and p.is_alive())
    @property
    def model_loaded(self)->bool: return self.state in {CandidateState.READY,CandidateState.BUSY}
    @property
    def generation(self)->int: return self._generation
    @property
    def restart_count(self)->int: return self._restarts

    def submit(self,audio:list[int],sample_rate:int,primary_text:str,*,interaction_id:str|None=None)->bool:
        job=CandidateJob(interaction_id or str(uuid4()),tuple(audio),sample_rate,primary_text)
        with self._lock:
            if self._closed.is_set() or self._state in {
                CandidateState.STOPPED, CandidateState.STARTING,
                CandidateState.DEGRADED, CandidateState.STOPPING,
            }:
                self._emit(job,"",0.,"not_ready",True); return False
            if self._state is CandidateState.BUSY or self._active is not None:
                self._emit(job,"",0.,"queue_full",True); return False
            task_id=uuid4().hex
            try: self._commands.put_nowait({"v":1,"kind":"transcribe","generation":self._generation,
                "task_id":task_id,"pcm":job.pcm,"sample_rate":sample_rate})
            except (Full,BrokenPipeError,OSError): self._emit(job,"",0.,"queue_full",True); return False
            self._active=(job,task_id,self._clock()); self._state=CandidateState.BUSY
        self._wake.set(); return True

    def _start(self)->None:
        self._generation+=1; self._commands=self._context.Queue(maxsize=1); self._results=self._context.Queue(maxsize=4)
        self._process=self._context.Process(target=candidate_process_main,
            args=(self._commands,self._results,self._generation,self._config,self._model_factory),
            name="stt-candidate-shadow",daemon=False)
        self._state=CandidateState.STARTING; self._started_at=self._clock(); self._process.start()
    def _supervise(self)->None:
        with self._lock: self._start()
        while not self._closed.is_set():
            self._poll(); self._wake.wait(.01); self._wake.clear()
        self._shutdown_generation()
    def _poll(self)->None:
        with self._lock: process,results=self._process,self._results
        if process is None:return
        while True:
            try: message=results.get_nowait()
            except (Empty, ValueError):break
            except (EOFError,OSError): self._fail_generation("ipc_corrupt"); return
            self._handle(message)
            if results is not self._results:return
        with self._lock:
            process,state,active=self._process,self._state,self._active
        if process is None:return
        if not process.is_alive(): self._fail_generation("process_exit"); return
        now=self._clock()
        if state is CandidateState.STARTING and now-self._started_at>self.startup_timeout_seconds:
            self._fail_generation("startup_timeout"); return
        if active is not None and now-active[2]>self.timeout_seconds:
            self._emit(active[0],"",(now-active[2])*1000,"timeout",False); self._fail_generation("timeout",True)
    def _handle(self,message:Any)->None:
        if not isinstance(message,dict) or message.get("v")!=1: self._fail_generation("ipc_corrupt"); return
        if message.get("generation")!=self._generation:return
        kind=message.get("kind")
        if kind=="ready":
            with self._lock:self._state=CandidateState.READY
            return
        if kind in {"fatal","error"}:self._fail_generation(str(message.get("code") or "candidate_error"));return
        if kind!="result":return
        with self._lock:
            active=self._active
            if active is None or message.get("task_id")!=active[1]:return
            self._active=None;self._state=CandidateState.READY
        code=message.get("code")
        payload=message.get("result")
        text=(payload.get("text") if isinstance(payload,dict) else message.get("text"))
        self._emit(active[0],str(text or ""),(self._clock()-active[2])*1000,code,False)
        if code=="out_of_memory":self._fail_generation("out_of_memory",True)
    def _fail_generation(self,code:str,emitted:bool=False)->None:
        with self._lock: active=self._active;self._active=None;self._state=CandidateState.DEGRADED
        if active is not None and not emitted:self._emit(active[0],"",(self._clock()-active[2])*1000,code,False)
        self._shutdown_generation()
        with self._lock:
            if not self._closed.is_set() and self._restarts<self.max_restarts:
                self._restarts+=1;self._start()
            elif not self._closed.is_set():self._state=CandidateState.DEGRADED
    def _shutdown_generation(self)->None:
        with self._lock:
            process,commands=self._process,self._commands
            if process is None:return
            self._state=CandidateState.STOPPING
        if process.is_alive() and commands is not None:
            try:commands.put_nowait({"v":1,"kind":"shutdown"})
            except (Full,BrokenPipeError,OSError):pass
            process.join(self.shutdown_timeout_seconds)
        if process.is_alive():process.terminate();process.join(self.shutdown_timeout_seconds)
        if process.is_alive() and hasattr(process,"kill"):process.kill();process.join(self.shutdown_timeout_seconds)
        for queue in (self._commands,self._results):
            if queue is not None:
                try:
                    queue.close()
                    # The worker has stopped: buffered commands have no consumer.
                    # Joining its feeder here can block forever on a full pipe.
                    queue.cancel_join_thread()
                except (AssertionError, OSError, ValueError):pass
        try:process.close()
        except (ValueError,OSError):pass
        with self._lock:
            self._process=self._commands=self._results=None
            if self._closed.is_set():self._state=CandidateState.STOPPED
    def _emit(self,job:CandidateJob,candidate_text:str,latency_ms:float,error_code:str|None,dropped:bool)->None:
        primary,candidate=sanitize_text(job.primary_text),sanitize_text(candidate_text)
        self._logger.info({"interaction_id":job.interaction_id,"model":self.model_name,
            "primary_text":primary,"candidate_text":candidate,"latency_ms":round(latency_ms,3),
            "exact_agreement":primary==candidate,"semantic_agreement":asdict(parse_text(primary))==asdict(parse_text(candidate)),
            "error_code":error_code,"dropped":dropped,"generation":self._generation})
    def close(self,timeout_seconds:float|None=None)->None:
        if self._closed.is_set():return
        self._closed.set();self._wake.set();self._thread.join(timeout_seconds or self.shutdown_timeout_seconds*3+1)
        if self._thread.is_alive():raise RuntimeError("stt candidate supervisor did not stop")
        # A depleted generation may already have released its process before
        # close. The supervisor's final shutdown then has no process to stop.
        with self._lock:
            self._state=CandidateState.STOPPED
        for handler in tuple(self._logger.handlers):self._logger.removeHandler(handler);handler.close()
