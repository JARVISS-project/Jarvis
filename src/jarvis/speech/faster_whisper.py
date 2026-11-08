from collections.abc import Callable, Iterable, Sequence
from enum import Enum
import gc
import threading
import time
from typing import Any, Protocol

from jarvis.speech.base import SpeechToText, TranscriptionResult
from jarvis.speech.nvidia_dlls import register_nvidia_dll_directories


class _Segment(Protocol):
    text: str
    avg_logprob: float
    no_speech_prob: float


class _TranscriptionInfo(Protocol):
    language: str | None
    duration: float | None
    language_probability: float | None
    duration_after_vad: float | None


class _WhisperModel(Protocol):
    def transcribe(
        self,
        audio: Any,
        **kwargs: Any,
    ) -> tuple[Iterable[_Segment], _TranscriptionInfo]: ...


class SttLoadState(str, Enum):
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    STOPPED = "stopped"


class FasterWhisperSTT(SpeechToText):
    def __init__(
        self,
        model_name: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = "pt",
        runtime_options: Callable[[], tuple[str, ...]] | None = None,
        model_factory: Callable[..., _WhisperModel] | None = None,
        audio_converter: Callable[[Sequence[int]], Any] | None = None,
        initial_prompt: str | None = None,
        hotwords_provider: Callable[[], str] | None = None,
        hotwords_enabled: bool = True,
        vad_enabled: bool = True,
        vad_min_silence_duration_ms: int = 500,
        vad_speech_pad_ms: int = 200,
        vad_threshold: float = 0.5,
        cuda_available: Callable[[], bool] | None = None,
        allow_device_fallback: bool = True,
        preload_guard: Callable[[], None] | None = None,
        output: Callable[[str], None] = print,
    ) -> None:
        if vad_min_silence_duration_ms < 0 or vad_speech_pad_ms < 0:
            raise ValueError("Durações do VAD não podem ser negativas.")
        if not 0.0 <= vad_threshold <= 1.0:
            raise ValueError("vad_threshold deve estar entre 0 e 1.")
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.runtime_options = runtime_options
        self._model_factory = model_factory
        self._audio_converter = audio_converter
        self._model: _WhisperModel | None = None
        self.initial_prompt = initial_prompt
        self.hotwords_provider = hotwords_provider
        self.hotwords_enabled = hotwords_enabled
        self.vad_enabled = vad_enabled
        self.vad_parameters = {
            "min_silence_duration_ms": vad_min_silence_duration_ms,
            "speech_pad_ms": vad_speech_pad_ms,
            "threshold": vad_threshold,
        }
        self._cuda_available = cuda_available or self._detect_cuda
        self.allow_device_fallback = allow_device_fallback
        self.preload_guard = preload_guard
        self.output = output
        self._fallback_announced = False
        self._cuda_failed = False
        self._loaded_options: tuple[str, str, str] | None = None
        self._load_state = SttLoadState.STOPPED
        self._load_error: str | None = None
        self._preload_done = threading.Event()
        self._preload_cancel = threading.Event()
        self._preload_thread: threading.Thread | None = None
        self._load_lock = threading.RLock()

    @property
    def is_model_loaded(self) -> bool:
        return self._model is not None

    @property
    def load_state(self) -> SttLoadState:
        with self._load_lock:
            return self._load_state

    @property
    def load_error(self) -> str | None:
        with self._load_lock:
            return self._load_error

    @property
    def effective_options(self) -> tuple[str, str, str] | None:
        return self._loaded_options

    def transcribe(
        self,
        audio: Sequence[int],
        sample_rate: int,
    ) -> TranscriptionResult:
        return self._transcribe(audio, sample_rate, beam_size=1)

    def transcribe_retry(
        self,
        audio: Sequence[int],
        sample_rate: int,
    ) -> TranscriptionResult:
        return self._transcribe(audio, sample_rate, beam_size=5)

    def _transcribe(
        self,
        audio: Sequence[int],
        sample_rate: int,
        *,
        beam_size: int,
    ) -> TranscriptionResult:
        if not audio:
            return TranscriptionResult("", None, 0.0)
        if sample_rate != 16000:
            raise ValueError("FasterWhisperSTT requer áudio PCM de 16 kHz.")

        model = self._ensure_model()
        converted_audio = self._convert_audio(audio)
        transcription_options: dict[str, Any] = {
            "beam_size": beam_size,
            "condition_on_previous_text": False,
            "vad_filter": self.vad_enabled,
        }
        if self.vad_enabled:
            transcription_options["vad_parameters"] = self.vad_parameters.copy()
        if self.language is not None:
            transcription_options["language"] = self.language
        if self.initial_prompt:
            transcription_options["initial_prompt"] = self.initial_prompt
        if self.hotwords_enabled and self.hotwords_provider is not None:
            hotwords = self.hotwords_provider().strip()
            if hotwords:
                transcription_options["hotwords"] = hotwords
        segments, info = model.transcribe(
            converted_audio,
            **transcription_options,
        )
        segment_list = list(segments)
        text = " ".join(
            segment.text.strip()
            for segment in segment_list
            if segment.text.strip()
        )
        log_probabilities = [
            value
            for segment in segment_list
            if (value := getattr(segment, "avg_logprob", None)) is not None
        ]
        no_speech_probabilities = [
            value
            for segment in segment_list
            if (value := getattr(segment, "no_speech_prob", None)) is not None
        ]
        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", None),
            duration_seconds=getattr(info, "duration", None),
            average_log_probability=(
                sum(log_probabilities) / len(log_probabilities)
                if log_probabilities else None
            ),
            no_speech_probability=(
                max(no_speech_probabilities)
                if no_speech_probabilities else None
            ),
            language_probability=getattr(info, "language_probability", None),
            duration_after_vad_seconds=getattr(
                info, "duration_after_vad", None
            ),
        )

    def warm_up(self) -> None:
        """Carrega o modelo e verifica o backend com PCM sintético em memória."""
        self._verify_loaded_model()

    def preload(self, timeout_seconds: float = 30.0) -> bool:
        if timeout_seconds <= 0:
            raise ValueError("Timeout de preload deve ser positivo.")
        with self._load_lock:
            if self._load_state is SttLoadState.READY:
                return True
            if self._load_state is not SttLoadState.LOADING:
                self._load_state = SttLoadState.LOADING
                self._load_error = None
                self._preload_done.clear()
                self._preload_cancel.clear()
                self._preload_thread = threading.Thread(
                    target=self._preload_worker,
                    name="jarvis-stt-preload",
                    daemon=True,
                )
                self._preload_thread.start()
        deadline = time.monotonic() + timeout_seconds
        while not self._preload_done.wait(0.05):
            if self._preload_cancel.is_set():
                return False
            if time.monotonic() >= deadline:
                with self._load_lock:
                    self._load_state = SttLoadState.FAILED
                    self._load_error = "load_timeout"
                self._preload_cancel.set()
                return False
        return self.load_state is SttLoadState.READY

    def _preload_worker(self) -> None:
        try:
            if self.preload_guard is not None:
                self.preload_guard()
            self._verify_loaded_model()
        except Exception as error:
            with self._load_lock:
                self._load_state = SttLoadState.FAILED
                self._load_error = type(error).__name__
        else:
            with self._load_lock:
                if self._preload_cancel.is_set():
                    self.unload()
                    self._load_state = SttLoadState.STOPPED
                else:
                    self._load_state = SttLoadState.READY
                    if self._loaded_options is not None:
                        model, device, compute_type = self._loaded_options
                        self.output(
                            "[stt] state=ready "
                            f"model={model} device={device} "
                            f"compute_type={compute_type}"
                        )
        finally:
            self._preload_done.set()

    def _verify_loaded_model(self) -> None:
        model = self._ensure_model()
        synthetic = self._convert_audio([0] * 1600)
        segments, _info = model.transcribe(
            synthetic,
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=False,
            language=self.language,
        )
        list(segments)

    def unload(self) -> None:
        self._model = None
        self._loaded_options = None
        gc.collect()

    def close(self) -> None:
        self._preload_cancel.set()
        self.unload()
        with self._load_lock:
            self._load_state = SttLoadState.STOPPED

    def _ensure_model(self) -> _WhisperModel:
        raw_options = (
            self.runtime_options()
            if self.runtime_options is not None
            else (self.device, self.compute_type)
        )
        if len(raw_options) == 3:
            model_name, requested_device, requested_compute = raw_options
        else:
            model_name = self.model_name
            requested_device, requested_compute = raw_options
        allow_fallback = (
            self.allow_device_fallback
            and requested_device in {"auto", "cuda"}
        )
        if requested_device in {"auto", "cuda"}:
            register_nvidia_dll_directories()
        model_name, device, compute_type = self._resolve_options(
            model_name, requested_device, requested_compute
        )
        options = (model_name, device, compute_type)
        if self._model is not None and self._loaded_options != options:
            self.unload()

        if self._model is None:
            if self._model_factory is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as error:
                    raise RuntimeError(
                        "Speech-to-Text não pôde ser carregado. "
                        "Verifique o extra 'voice', dependências nativas e "
                        f"a política de controle de aplicativos: {error}."
                    ) from error
                self._model_factory = WhisperModel

            try:
                self._model = self._model_factory(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                )
            except Exception:
                if not allow_fallback or device != "cuda":
                    raise
                self._announce_fallback()
                self._cuda_failed = True
                model_name, device, compute_type = "small", "cpu", "int8"
                options = (model_name, device, compute_type)
                self._model = self._model_factory(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                )
            self._loaded_options = options

        return self._model

    def _resolve_options(
        self,
        model_name: str,
        device: str,
        compute_type: str,
    ) -> tuple[str, str, str]:
        if device != "auto":
            resolved_compute = (
                "float16" if compute_type == "auto" and device == "cuda"
                else "int8" if compute_type == "auto"
                else compute_type
            )
            return model_name, device, resolved_compute
        if not self._cuda_failed and self._cuda_available():
            return model_name, "cuda", (
                "float16" if compute_type == "auto" else compute_type
            )
        self._announce_fallback()
        self._cuda_failed = True
        return "small", "cpu", "int8"

    def _announce_fallback(self) -> None:
        if self._fallback_announced:
            return
        self.output("[stt] CUDA indisponível; usando fallback CPU.")
        self._fallback_announced = True

    @staticmethod
    def _detect_cuda() -> bool:
        register_nvidia_dll_directories()
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except (ImportError, OSError, RuntimeError):
            return False

    def _convert_audio(self, audio: Sequence[int]) -> Any:
        if self._audio_converter is not None:
            return self._audio_converter(audio)

        try:
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                "NumPy é necessário para transcrever áudio PCM."
            ) from error
        return np.asarray(audio, dtype=np.float32) / 32768.0
