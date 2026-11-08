"""Spawn-safe persistent STT worker shared by primary and shadow policies."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import multiprocessing as mp
from queue import Empty, Full
import threading
import time
from typing import Any, Callable, Sequence
from uuid import uuid4

from jarvis.speech.base import SpeechToText, TranscriptionResult

PROTOCOL_VERSION = 1


class SttProcessState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True)
class SttModelConfig:
    model_name: str
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = "pt"
    vad_enabled: bool = True
    vad_min_silence_duration_ms: int = 500
    vad_speech_pad_ms: int = 200
    vad_threshold: float = .5
    initial_prompt: str | None = None
    hotwords: str | None = None


class SttProcessError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"STT indisponível (motivo: {reason_code}).")


def default_model_factory(config: SttModelConfig) -> SpeechToText:
    from jarvis.speech.faster_whisper import FasterWhisperSTT
    return FasterWhisperSTT(
        model_name=config.model_name, device=config.device,
        compute_type=config.compute_type, language=config.language,
        allow_device_fallback=False, vad_enabled=config.vad_enabled,
        vad_min_silence_duration_ms=config.vad_min_silence_duration_ms,
        vad_speech_pad_ms=config.vad_speech_pad_ms,
        vad_threshold=config.vad_threshold,
        initial_prompt=config.initial_prompt,
        hotwords_provider=(lambda: config.hotwords or ""),
        hotwords_enabled=bool(config.hotwords),
    )


def safe_reason(error: BaseException) -> str:
    value = (type(error).__name__ + " " + str(error)).casefold()
    if isinstance(error, MemoryError) or "outofmemory" in value or "out of memory" in value:
        return "out_of_memory"
    return "model_error"


def stt_process_main(
    commands: Any,
    results: Any,
    generation: int,
    config: SttModelConfig,
    model_factory: Callable[[SttModelConfig], Any] = default_model_factory,
) -> None:
    """Child boundary: inert config + PCM in, serializable result out."""
    model = None
    try:
        model = model_factory(config)
        warm_up = getattr(model, "warm_up", None)
        if callable(warm_up): warm_up()
        results.put({"v": PROTOCOL_VERSION, "kind": "ready", "generation": generation})
        while True:
            message = commands.get()
            if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
                results.put({"v": PROTOCOL_VERSION, "kind": "error", "generation": generation,
                             "code": "ipc_corrupt"})
                continue
            kind = message.get("kind")
            if kind == "shutdown":
                results.put({"v": PROTOCOL_VERSION, "kind": "stopped", "generation": generation})
                return
            if kind != "transcribe":
                results.put({"v": PROTOCOL_VERSION, "kind": "error", "generation": generation,
                             "code": "ipc_corrupt"})
                continue
            try:
                operation = model.transcribe_retry if message.get("retry") else model.transcribe
                transcription = operation(message["pcm"], message["sample_rate"])
                payload = asdict(transcription)
                code = None
            except BaseException as error:
                payload = asdict(TranscriptionResult("", None, 0.0))
                code = safe_reason(error)
            results.put({
                "v": PROTOCOL_VERSION, "kind": "result", "generation": generation,
                "task_id": message.get("task_id"), "result": payload, "code": code,
            })
    except BaseException as error:
        try:
            results.put({"v": PROTOCOL_VERSION, "kind": "fatal", "generation": generation,
                         "code": safe_reason(error)})
        except Exception:
            pass
    finally:
        if model is not None:
            try: model.close()
            except Exception: pass


class SttProcessClient(SpeechToText):
    """Authoritative synchronous policy over a disposable persistent worker."""
    def __init__(
        self, config: SttModelConfig, *, timeout_seconds: float = 20.0,
        startup_timeout_seconds: float = 30.0, shutdown_timeout_seconds: float = 3.0,
        max_restarts: int = 2, context: Any | None = None,
        model_factory: Callable[[SttModelConfig], Any] = default_model_factory,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(timeout_seconds, startup_timeout_seconds, shutdown_timeout_seconds) <= 0:
            raise ValueError("Timeouts do processo STT devem ser positivos.")
        if max_restarts < 0:
            raise ValueError("Limite de reinícios STT inválido.")
        self.config = config; self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.max_restarts = max_restarts; self._context = context or mp.get_context("spawn")
        self._model_factory = model_factory; self._clock = clock
        self._lock = threading.RLock(); self._state = SttProcessState.STOPPED
        self._generation = 0; self._restarts = 0; self._closed = False
        self._process = self._commands = self._results = None

    @property
    def state(self) -> SttProcessState:
        with self._lock: return self._state

    @property
    def worker_alive(self) -> bool:
        process = self._process
        return bool(process is not None and process.is_alive())

    @property
    def worker_pid(self) -> int | None:
        process = self._process
        return None if process is None else process.pid

    @property
    def generation(self) -> int: return self._generation

    @property
    def restart_count(self) -> int: return self._restarts

    @property
    def is_model_loaded(self) -> bool:
        return self.state in {SttProcessState.READY, SttProcessState.BUSY}

    def warm_up(self) -> None:
        with self._lock:
            if self._closed: raise SttProcessError("stopped")
            if self._state is SttProcessState.READY: return
            self._start_generation()
        deadline = self._clock() + self.startup_timeout_seconds
        while self._clock() < deadline:
            message = self._receive(.02)
            if message is None:
                if not self.worker_alive: return self._fail("process_exit")
                continue
            if not self._valid(message): return self._fail("ipc_corrupt")
            if message.get("generation") != self._generation: continue
            if message.get("kind") == "ready":
                with self._lock: self._state = SttProcessState.READY
                return
            return self._fail(str(message.get("code") or "startup_error"))
        return self._fail("startup_timeout")

    def transcribe(self, audio: Sequence[int], sample_rate: int) -> TranscriptionResult:
        return self._transcribe(audio, sample_rate, retry=False)

    def transcribe_retry(self, audio: Sequence[int], sample_rate: int) -> TranscriptionResult:
        return self._transcribe(audio, sample_rate, retry=True)

    def _transcribe(self, audio: Sequence[int], sample_rate: int, *, retry: bool) -> TranscriptionResult:
        if not audio: return TranscriptionResult("", None, 0.0)
        with self._lock:
            if self._state is SttProcessState.BUSY:
                raise SttProcessError("queue_full")
            if self._state is not SttProcessState.READY:
                raise SttProcessError("not_ready")
            task_id = uuid4().hex
            try:
                self._commands.put_nowait({
                    "v": PROTOCOL_VERSION, "kind": "transcribe", "generation": self._generation,
                    "task_id": task_id, "pcm": tuple(audio), "sample_rate": sample_rate,
                    "retry": retry,
                })
            except (Full, BrokenPipeError, OSError):
                raise SttProcessError("queue_full")
            self._state = SttProcessState.BUSY
        deadline = self._clock() + self.timeout_seconds
        while self._clock() < deadline:
            message = self._receive(.01)
            if message is None:
                if not self.worker_alive: return self._fail("process_exit")
                continue
            if not self._valid(message): return self._fail("ipc_corrupt")
            if message.get("generation") != self._generation: continue
            if message.get("kind") != "result": return self._fail(
                str(message.get("code") or "ipc_corrupt")
            )
            if message.get("task_id") != task_id: continue
            code = message.get("code")
            if code: return self._fail(str(code))
            payload = message.get("result")
            if not isinstance(payload, dict): return self._fail("ipc_corrupt")
            try: result = TranscriptionResult(**payload)
            except (TypeError, ValueError): return self._fail("ipc_corrupt")
            with self._lock: self._state = SttProcessState.READY
            return result
        return self._fail("timeout")

    def _start_generation(self) -> None:
        self._generation += 1
        self._commands = self._context.Queue(maxsize=1)
        self._results = self._context.Queue(maxsize=4)
        self._process = self._context.Process(
            target=stt_process_main,
            args=(self._commands, self._results, self._generation, self.config, self._model_factory),
            name="stt-primary", daemon=False,
        )
        self._state = SttProcessState.STARTING; self._process.start()

    def _receive(self, timeout: float) -> Any | None:
        try: return self._results.get(timeout=timeout)
        except Empty: return None
        except (EOFError, OSError, ValueError): return {"v": 0}

    @staticmethod
    def _valid(message: Any) -> bool:
        return isinstance(message, dict) and message.get("v") == PROTOCOL_VERSION

    def _fail(self, code: str) -> None:
        with self._lock:
            if self._closed:
                self._state = SttProcessState.STOPPED
                raise SttProcessError(code)
            self._state = SttProcessState.DEGRADED
        self._shutdown_generation()
        with self._lock:
            if not self._closed and self._restarts < self.max_restarts:
                self._restarts += 1
                try:
                    self._start_generation()
                    deadline = self._clock() + self.startup_timeout_seconds
                    while self._clock() < deadline:
                        message = self._receive(.02)
                        if message is None:
                            if not self.worker_alive: break
                            continue
                        if (
                            self._valid(message)
                            and message.get("generation") == self._generation
                            and message.get("kind") == "ready"
                        ):
                            self._state = SttProcessState.READY
                        break
                    if self._state is not SttProcessState.READY:
                        self._shutdown_generation()
                        self._state = SttProcessState.FAILED
                except Exception: self._state = SttProcessState.FAILED
            elif not self._closed: self._state = SttProcessState.FAILED
        raise SttProcessError(code)

    def _shutdown_generation(self) -> None:
        with self._lock:
            process, commands = self._process, self._commands
            if process is None: return
            self._state = SttProcessState.STOPPING
        if process.is_alive() and commands is not None:
            try: commands.put_nowait({"v": PROTOCOL_VERSION, "kind": "shutdown"})
            except (Full, BrokenPipeError, OSError): pass
            process.join(self.shutdown_timeout_seconds)
        if process.is_alive(): process.terminate(); process.join(self.shutdown_timeout_seconds)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill(); process.join(self.shutdown_timeout_seconds)
        for channel in (self._commands, self._results):
            if channel is not None:
                try: channel.close(); channel.cancel_join_thread()
                except (OSError, ValueError): pass
        try: process.close()
        except (OSError, ValueError): pass
        with self._lock: self._process = self._commands = self._results = None

    def close(self) -> None:
        with self._lock:
            if self._closed: return
            self._closed = True
        self._shutdown_generation()
        with self._lock: self._state = SttProcessState.STOPPED
