from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Queue
from threading import Event, Lock, Thread, get_ident
from typing import Any, Literal, Protocol

from jarvis.tts.base import SpeechPlaybackHandle, TextToSpeech


class _SpeechEngine(Protocol):
    def setProperty(self, name: str, value: Any) -> None: ...

    def say(self, text: str) -> None: ...

    def runAndWait(self) -> None: ...

    def stop(self) -> None: ...


@dataclass
class _TTSRequest:
    action: Literal["speak", "stop"]
    text: str = ""
    completed: Event = field(default_factory=Event)
    error: Exception | None = None


_SHUTDOWN = object()


class WindowsSapiTTS(TextToSpeech):
    """Synchronous TTS facade over one dedicated SAPI5 worker thread."""

    def __init__(
        self,
        rate: int = 180,
        volume: float = 1.0,
        voice_id: str | None = None,
        engine_factory: Callable[[], _SpeechEngine] | None = None,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate deve ser positivo.")
        if not 0.0 <= volume <= 1.0:
            raise ValueError("volume deve estar entre 0.0 e 1.0.")

        self.rate = rate
        self.volume = volume
        self.voice_id = voice_id
        self._engine_factory = engine_factory
        self._engine: _SpeechEngine | None = None
        self._requests: Queue[_TTSRequest | object] = Queue()
        self._worker: Thread | None = None
        self._worker_ident: int | None = None
        self._lifecycle_lock = Lock()
        self._closed = False
        self._speaking = Event()
        self._stop_requested = Event()

    @property
    def is_engine_initialized(self) -> bool:
        return self._engine is not None

    @property
    def is_worker_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    @property
    def worker_ident(self) -> int | None:
        return self._worker_ident

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        self.speak_async(text).wait()

    def speak_async(self, text: str) -> SpeechPlaybackHandle:
        request = _TTSRequest(action="speak", text=text)
        if not text.strip():
            request.completed.set()
            return SpeechPlaybackHandle(request.completed, self.stop)
        self._ensure_worker()
        self._requests.put(request)
        return SpeechPlaybackHandle(
            request.completed,
            self.stop,
            lambda: request.error,
        )

    def stop(self) -> None:
        if self._speaking.is_set():
            self._stop_requested.set()
        elif self.is_worker_running:
            self._submit(_TTSRequest(action="stop"))

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            worker = self._worker

        if worker is not None:
            self._requests.put(_SHUTDOWN)
            worker.join()

        with self._lifecycle_lock:
            self._worker = None

    def _submit(self, request: _TTSRequest) -> None:
        self._ensure_worker()
        self._requests.put(request)
        request.completed.wait()
        if request.error is not None:
            raise request.error

    def _ensure_worker(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("O Text-to-Speech já foi fechado.")
            if self._worker is not None:
                return
            self._worker = Thread(
                target=self._worker_main,
                name="jarvis-sapi5",
                daemon=False,
            )
            self._worker.start()

    def _worker_main(self) -> None:
        self._worker_ident = get_ident()
        pythoncom = self._initialize_com()
        try:
            while True:
                request = self._requests.get()
                if request is _SHUTDOWN:
                    break
                if not isinstance(request, _TTSRequest):
                    continue
                self._process_request(request)
        finally:
            self._discard_engine()
            if pythoncom is not None:
                pythoncom.CoUninitialize()
            self._worker_ident = None

    def _process_request(self, request: _TTSRequest) -> None:
        try:
            if request.action == "stop":
                if self._engine is not None:
                    self._engine.stop()
                return

            engine = self._ensure_engine()
            self._prepare_engine_queue(engine)
            engine.say(request.text)
            self._speaking.set()
            self._stop_requested.clear()
            self._run_speech(engine)
        except Exception as error:
            request.error = error
            self._discard_engine()
        finally:
            self._speaking.clear()
            self._stop_requested.clear()
            request.completed.set()

    def _run_speech(self, engine: _SpeechEngine) -> None:
        start_loop = getattr(engine, "startLoop", None)
        iterate = getattr(engine, "iterate", None)
        end_loop = getattr(engine, "endLoop", None)
        is_busy = getattr(engine, "isBusy", None)
        if not all(callable(method) for method in (
            start_loop, iterate, end_loop, is_busy
        )):
            engine.runAndWait()
            return
        start_loop(False)
        try:
            while is_busy():
                if self._stop_requested.is_set():
                    engine.stop()
                    break
                iterate()
                self._stop_requested.wait(0.01)
        finally:
            end_loop()

    def _ensure_engine(self) -> _SpeechEngine:
        if self._engine is None:
            engine_factory = self._engine_factory
            if engine_factory is None:
                try:
                    import pyttsx3
                except ImportError as error:
                    raise RuntimeError(
                        "Text-to-Speech não instalado. "
                        "Instale o extra 'voice'."
                    ) from error
                engine_factory = lambda: pyttsx3.init(driverName="sapi5")
                self._engine_factory = engine_factory

            engine = engine_factory()
            try:
                engine.setProperty("rate", self.rate)
                engine.setProperty("volume", self.volume)
                if self.voice_id is not None:
                    engine.setProperty("voice", self.voice_id)
            except Exception:
                try:
                    engine.stop()
                except Exception:
                    pass
                raise
            self._engine = engine

        return self._engine

    def _discard_engine(self) -> None:
        engine = self._engine
        self._engine = None
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass

    @staticmethod
    def _prepare_engine_queue(engine: _SpeechEngine) -> None:
        """Keep pyttsx3 2.99 commands queued until runAndWait starts."""
        proxy = getattr(engine, "proxy", None)
        set_busy = getattr(proxy, "setBusy", None)
        if callable(set_busy):
            set_busy(True)

    @staticmethod
    def _initialize_com() -> Any | None:
        try:
            import pythoncom
        except ImportError:
            return None
        try:
            pythoncom.CoInitialize()
        except Exception:
            return None
        return pythoncom
