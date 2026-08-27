from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread
import time
from typing import Any, Protocol

from jarvis.tts.base import SpeechPlaybackHandle, TextToSpeech


class _PiperChunk(Protocol):
    sample_rate: int
    audio_float_array: Any


class _PiperVoice(Protocol):
    def synthesize(self, text: str) -> Iterable[_PiperChunk]: ...


class _AudioPlayer(Protocol):
    def play(self, audio: Any, sample_rate: int) -> None: ...

    def stop(self) -> None: ...


class _SoundDevicePlayer:
    def play(self, audio: Any, sample_rate: int) -> None:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError(
                "Playback Piper indisponível. Instale o extra 'fast-voice'."
            ) from error

        data = np.asarray(audio, dtype=np.float32)
        data = np.squeeze(data)
        sd.play(data, samplerate=sample_rate, blocking=False)
        sd.wait()

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


class PiperTTS(TextToSpeech):
    """Fast local pt-BR TTS intended for normal conversation.

    Piper stays on CPU so Whisper/Ollama can keep the GPU. The ONNX voice is
    loaded lazily on the first utterance and kept in memory for later turns.
    """

    fast_acknowledgement = True

    def __init__(
        self,
        *,
        model_path: Path,
        config_path: Path | None = None,
        fallback: TextToSpeech | None = None,
        voice_factory: Callable[[Path, Path | None], _PiperVoice] | None = None,
        audio_player: _AudioPlayer | None = None,
        tail_silence_seconds: float = 0.30,
        length_scale: float = 1.18,
        sentence_silence_seconds: float = 0.18,
    ) -> None:
        self.model_path = Path(model_path)
        self.config_path = Path(config_path) if config_path is not None else None
        self.fallback = fallback
        self._voice_factory = voice_factory
        self._audio_player = audio_player or _SoundDevicePlayer()
        self.tail_silence_seconds = max(0.0, float(tail_silence_seconds))
        self.length_scale = max(0.5, float(length_scale))
        self.sentence_silence_seconds = max(
            0.0, float(sentence_silence_seconds)
        )
        self._voice: _PiperVoice | None = None
        self._requests: Queue[_TTSRequest | object] = Queue()
        self._worker: Thread | None = None
        self._lifecycle_lock = Lock()
        self._closed = False
        self._stop_requested = Event()
        self._current_request: _TTSRequest | None = None
        self._primary_disabled = False
        self._last_primary_error: Exception | None = None
        self._last_synthesis_seconds: float | None = None

    @property
    def is_model_loaded(self) -> bool:
        return self._voice is not None

    @property
    def is_ready(self) -> bool:
        config_path = self.config_path or Path(f"{self.model_path}.json")
        return self.model_path.is_file() and config_path.is_file()

    @property
    def last_primary_error(self) -> Exception | None:
        return self._last_primary_error

    @property
    def last_synthesis_seconds(self) -> float | None:
        return self._last_synthesis_seconds

    def warm_up(self) -> None:
        self._ensure_voice()

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
        self._worker = None
        self._voice = None
        if self.fallback is not None:
            self.fallback.close()

    def _ensure_worker(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("O Text-to-Speech já foi fechado.")
            if self._worker is not None:
                return
            self._worker = Thread(
                target=self._worker_main,
                name="jarvis-piper",
                daemon=False,
            )
            self._worker.start()

    def _worker_main(self) -> None:
        while True:
            request = self._requests.get()
            if request is _SHUTDOWN:
                break
            if isinstance(request, _TTSRequest):
                self._process_request(request)

    def _process_request(self, request: _TTSRequest) -> None:
        self._current_request = request
        self._stop_requested.clear()
        self._last_synthesis_seconds = None
        try:
            if self._primary_disabled:
                self._speak_with_fallback(request)
                return
            try:
                voice = self._ensure_voice()
                synthesis_started = time.perf_counter()
                chunks = list(voice.synthesize(request.text))
                self._last_synthesis_seconds = (
                    time.perf_counter() - synthesis_started
                )
                if self._stop_requested.is_set() or request.interrupted:
                    return
                if not chunks:
                    return

                try:
                    import numpy as np
                except ImportError as error:
                    raise RuntimeError(
                        "NumPy indisponível para o playback do Piper."
                    ) from error

                sample_rate = chunks[0].sample_rate
                sentence_silence_samples = int(
                    sample_rate * self.sentence_silence_seconds
                )
                audio_parts: list[Any] = []
                for chunk in chunks:
                    if chunk.sample_rate != sample_rate:
                        continue
                    if audio_parts and sentence_silence_samples > 0:
                        audio_parts.append(
                            np.zeros(sentence_silence_samples, dtype=np.float32)
                        )
                    audio_parts.append(
                        np.asarray(
                            chunk.audio_float_array, dtype=np.float32
                        ).reshape(-1)
                    )
                if not audio_parts:
                    return
                audio = (
                    audio_parts[0]
                    if len(audio_parts) == 1
                    else np.concatenate(audio_parts)
                )
                # Some audio devices/drivers clip the final phoneme when the
                # stream ends exactly on the last generated sample. Keep a
                # short silent tail inside the same blocking playback so
                # playback.wait() only completes after the device has drained.
                tail_samples = int(sample_rate * self.tail_silence_seconds)
                if tail_samples > 0:
                    audio = np.concatenate(
                        (audio, np.zeros(tail_samples, dtype=np.float32))
                    )
                request.started.set()
                self._audio_player.play(audio, sample_rate)
            except Exception as error:
                self._last_primary_error = error
                self._primary_disabled = True
                self._voice = None
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
                    "Piper indisponível e nenhum TTS de fallback foi configurado."
                ) from primary_error
            raise RuntimeError("Piper indisponível.")
        if self._stop_requested.is_set() or request.interrupted:
            return
        request.started.set()
        fallback.speak(request.text)

    def _ensure_voice(self) -> _PiperVoice:
        if self._voice is not None:
            return self._voice
        config_path = self.config_path or Path(f"{self.model_path}.json")
        if not self.model_path.is_file() or not config_path.is_file():
            raise RuntimeError(
                "Voz rápida não preparada. Rode: "
                "python -m jarvis --setup-fast-voice"
            )
        factory = self._voice_factory or self._default_voice_factory
        self._voice = factory(self.model_path, config_path)
        # Piper uses length_scale > 1.0 to slow phoneme duration. Keep this
        # on the loaded voice so every synthesis call uses the conversational
        # pace without requiring a specific Piper package API version.
        voice_config = getattr(self._voice, "config", None)
        if voice_config is not None and hasattr(voice_config, "length_scale"):
            voice_config.length_scale = self.length_scale
        return self._voice

    @staticmethod
    def _default_voice_factory(
        model_path: Path,
        config_path: Path | None,
    ) -> _PiperVoice:
        try:
            from piper import PiperVoice
        except ImportError as error:
            raise RuntimeError(
                "Piper não instalado. Instale com: "
                "python -m pip install -e \".[fast-voice]\""
            ) from error
        return PiperVoice.load(
            model_path,
            config_path=config_path,
            use_cuda=False,
        )

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
