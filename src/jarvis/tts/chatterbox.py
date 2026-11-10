from __future__ import annotations

from collections.abc import Callable
from inspect import Parameter, signature
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread
from typing import Any, Protocol

from jarvis.tts.base import SpeechPlaybackHandle, TextToSpeech
from jarvis.tts.playback_activity import play_with_activity
from jarvis.tts.chatterbox_process import (
    ChatterboxProcessClient,
    ChatterboxProcessConfig,
    ChatterboxProcessInterrupted,
)


class _ChatterboxModel(Protocol):
    sr: int

    def generate(self, text: str, **kwargs: Any) -> Any: ...


class _AudioPlayer(Protocol):
    def play(self, audio: Any, sample_rate: int) -> None: ...

    def stop(self) -> None: ...


class _SoundDevicePlayer:
    def __init__(
        self,
        level_callback: Callable[[float], None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._level_callback = level_callback
        self._stop_requested = stop_requested

    def play(self, audio: Any, sample_rate: int) -> None:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError(
                "Playback neural indisponível. Instale o extra 'neural-voice'."
            ) from error

        if hasattr(audio, "detach"):
            audio = audio.detach()
        if hasattr(audio, "cpu"):
            audio = audio.cpu()
        if hasattr(audio, "float"):
            audio = audio.float()
        if hasattr(audio, "numpy"):
            audio = audio.numpy()
        data = np.asarray(audio, dtype=np.float32)
        if data.ndim == 2 and data.shape[0] <= 2:
            data = data.T
        data = np.squeeze(data)
        play_with_activity(
            sd,
            data,
            sample_rate,
            level_callback=self._level_callback,
            stop_requested=self._stop_requested,
        )

    def stop(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            return
        sd.stop()


@dataclass
class _TTSRequest:
    text: str
    started: Event = field(default_factory=Event)
    finished: Event = field(default_factory=Event)
    interrupted: bool = False
    error: Exception | None = None


_SHUTDOWN = object()


class ChatterboxTTS(TextToSpeech):
    fast_acknowledgement = False
    """Lazy local neural TTS with optional SAPI fallback.

    Chatterbox is imported and its model is loaded only when the first utterance
    is spoken (or ``warm_up`` is called). This keeps wake-word idle mode light.
    """

    def __init__(
        self,
        *,
        device: str = "auto",
        language_id: str = "pt",
        reference_audio_path: Path | None = None,
        t3_model: str = "v3",
        exaggeration: float = 0.35,
        cfg_weight: float = 0.35,
        fallback: TextToSpeech | None = None,
        model_factory: Callable[[str, str], _ChatterboxModel] | None = None,
        audio_player: _AudioPlayer | None = None,
        process_client: ChatterboxProcessClient | None = None,
        activity_callback: Callable[[float], None] | None = None,
    ) -> None:
        self.device = device
        self.language_id = language_id
        self.reference_audio_path = reference_audio_path
        self.t3_model = t3_model
        self.exaggeration = exaggeration
        self.cfg_weight = cfg_weight
        self.fallback = fallback
        self._model_factory = model_factory
        self._stop_requested = Event()
        self._audio_player = audio_player or _SoundDevicePlayer(
            activity_callback, stop_requested=self._stop_requested.is_set
        )
        self._model: _ChatterboxModel | None = None
        self._process_client = process_client
        self._use_process_isolation = model_factory is None
        self._requests: Queue[_TTSRequest | object] = Queue()
        self._worker: Thread | None = None
        self._lifecycle_lock = Lock()
        self._closed = False
        self._current_request: _TTSRequest | None = None
        self._primary_disabled = False
        self._last_primary_error: Exception | None = None

    @property
    def is_model_loaded(self) -> bool:
        if self._use_process_isolation:
            return self._process_client is not None and self._process_client.is_running
        return self._model is not None

    @property
    def last_primary_error(self) -> Exception | None:
        return self._last_primary_error

    def warm_up(self) -> None:
        """Load/download the model without generating speech."""
        if self._use_process_isolation:
            self._ensure_process_client().warm_up()
            return
        self._ensure_model()

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        self.speak_async(text).wait()

    def speak_async(self, text: str) -> SpeechPlaybackHandle:
        request = _TTSRequest(text=text)
        if not text.strip():
            request.started.set()
            request.finished.set()
            return self._playback_handle(request)
        self._ensure_worker()
        self._requests.put(request)
        return self._playback_handle(request)

    def stop(self) -> None:
        request = self._current_request
        if request is not None:
            request.interrupted = True
        self._stop_requested.set()
        self._audio_player.stop()
        if self._process_client is not None:
            self._process_client.cancel()
        if self.fallback is not None:
            self.fallback.stop()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            worker = self._worker
        self.stop()
        if worker is not None:
            self._requests.put(_SHUTDOWN)
            worker.join()
        self._model = None
        self._worker = None
        if self._process_client is not None:
            self._process_client.close()
            self._process_client = None
        if self.fallback is not None:
            self.fallback.close()
        if not self._use_process_isolation:
            self._release_cuda_cache()

    def _ensure_worker(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("O Text-to-Speech já foi fechado.")
            if self._worker is not None:
                return
            self._worker = Thread(
                target=self._worker_main,
                name="jarvis-chatterbox",
                daemon=False,
            )
            self._worker.start()

    def _worker_main(self) -> None:
        while True:
            request = self._requests.get()
            if request is _SHUTDOWN:
                break
            if not isinstance(request, _TTSRequest):
                continue
            self._process_request(request)

    def _process_request(self, request: _TTSRequest) -> None:
        self._current_request = request
        self._stop_requested.clear()
        try:
            if self._primary_disabled:
                self._speak_with_fallback(request)
                return
            try:
                if self._use_process_isolation:
                    audio, sample_rate = self._ensure_process_client().generate(
                        request.text
                    )
                else:
                    model = self._ensure_model()
                    generate_kwargs: dict[str, Any] = {
                        "language_id": self.language_id,
                        "exaggeration": self.exaggeration,
                        "cfg_weight": self.cfg_weight,
                    }
                    if self.reference_audio_path is not None:
                        generate_kwargs["audio_prompt_path"] = str(
                            self.reference_audio_path
                        )
                    audio = model.generate(request.text, **generate_kwargs)
                    sample_rate = model.sr
                if self._stop_requested.is_set() or request.interrupted:
                    return
                request.started.set()
                self._audio_player.play(audio, sample_rate)
            except ChatterboxProcessInterrupted:
                return
            except Exception as error:
                self._last_primary_error = error
                self._primary_disabled = True
                self._model = None
                if self._process_client is not None:
                    self._process_client.close()
                    self._process_client = None
                if not self._use_process_isolation:
                    self._release_cuda_cache()
                self._speak_with_fallback(request, primary_error=error)
        except Exception as error:
            request.error = error
        finally:
            self._current_request = None
            self._stop_requested.clear()
            request.started.set()
            request.finished.set()

    def _speak_with_fallback(
        self,
        request: _TTSRequest,
        primary_error: Exception | None = None,
    ) -> None:
        fallback = self.fallback
        if fallback is None:
            if primary_error is not None:
                raise RuntimeError(
                    "Chatterbox indisponível e nenhum TTS de fallback foi configurado."
                ) from primary_error
            raise RuntimeError("TTS neural indisponível.")
        if self._stop_requested.is_set() or request.interrupted:
            return
        request.started.set()
        fallback.speak(request.text)

    def _ensure_process_client(self) -> ChatterboxProcessClient:
        if self._process_client is None:
            self._process_client = ChatterboxProcessClient(
                ChatterboxProcessConfig(
                    device=self.device,
                    language_id=self.language_id,
                    reference_audio_path=self.reference_audio_path,
                    t3_model=self.t3_model,
                    exaggeration=self.exaggeration,
                    cfg_weight=self.cfg_weight,
                )
            )
        return self._process_client

    def _ensure_model(self) -> _ChatterboxModel:
        if self._model is not None:
            return self._model
        device = self._resolve_device()
        factory = self._model_factory or self._default_model_factory
        if self.reference_audio_path is not None and self._model_factory is None:
            from jarvis.tts.reference import load_local_model
            self._model = load_local_model(device)
        else:
            self._model = factory(device, self.t3_model)
        return self._model

    @staticmethod
    def _default_model_factory(device: str, t3_model: str) -> _ChatterboxModel:
        try:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        except ImportError as error:
            raise RuntimeError(
                "Chatterbox não instalado. Instale com: "
                "python -m pip install -e \".[neural-voice]\""
            ) from error
        loader = ChatterboxMultilingualTTS.from_pretrained
        try:
            parameters = signature(loader).parameters.values()
            supports_t3_model = any(
                parameter.name == "t3_model"
                or parameter.kind is Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            supports_t3_model = False

        if supports_t3_model:
            return loader(device=device, t3_model=t3_model)

        # Older PyPI builds of chatterbox-tts do not expose the newer
        # ``t3_model`` selector yet. They still provide the multilingual
        # checkpoint, so load their default instead of failing startup.
        return loader(device=device)

    def _resolve_device(self) -> str:
        requested = self.device.casefold().strip()
        if requested != "auto":
            return requested
        try:
            import torch
        except ImportError:
            return "cpu"
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _playback_handle(self, request: _TTSRequest) -> SpeechPlaybackHandle:
        def stop_request() -> None:
            request.interrupted = True
            self.stop()

        return SpeechPlaybackHandle(
            request.finished,
            stop_request,
            lambda: request.error,
            started_event=request.started,
            interrupted_getter=lambda: request.interrupted,
        )

    @staticmethod
    def _release_cuda_cache() -> None:
        try:
            import torch
        except ImportError:
            return
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
