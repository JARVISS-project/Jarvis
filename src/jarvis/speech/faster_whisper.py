from collections.abc import Callable, Iterable, Sequence
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
        self.output = output
        self._fallback_announced = False
        self._cuda_failed = False
        self._loaded_options: tuple[str, str, str] | None = None

    @property
    def is_model_loaded(self) -> bool:
        return self._model is not None

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

    def unload(self) -> None:
        self._model = None
        self._loaded_options = None

    def close(self) -> None:
        self.unload()

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
        allow_fallback = requested_device in {"auto", "cuda"}
        if allow_fallback:
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
                        "Speech-to-Text não instalado. "
                        "Instale o extra 'voice'."
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
