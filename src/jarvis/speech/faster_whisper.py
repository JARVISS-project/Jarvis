from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

from jarvis.speech.base import SpeechToText, TranscriptionResult


class _Segment(Protocol):
    text: str


class _TranscriptionInfo(Protocol):
    language: str | None
    duration: float | None


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
        runtime_options: Callable[[], tuple[str, str]] | None = None,
        model_factory: Callable[..., _WhisperModel] | None = None,
        audio_converter: Callable[[Sequence[int]], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.runtime_options = runtime_options
        self._model_factory = model_factory
        self._audio_converter = audio_converter
        self._model: _WhisperModel | None = None
        self._loaded_options: tuple[str, str] | None = None

    @property
    def is_model_loaded(self) -> bool:
        return self._model is not None

    def transcribe(
        self,
        audio: Sequence[int],
        sample_rate: int,
    ) -> TranscriptionResult:
        if not audio:
            return TranscriptionResult("", None, 0.0)
        if sample_rate != 16000:
            raise ValueError("FasterWhisperSTT requer áudio PCM de 16 kHz.")

        model = self._ensure_model()
        converted_audio = self._convert_audio(audio)
        transcription_options: dict[str, Any] = {
            "beam_size": 1,
            "condition_on_previous_text": False,
        }
        if self.language is not None:
            transcription_options["language"] = self.language
        segments, info = model.transcribe(
            converted_audio,
            **transcription_options,
        )
        text = " ".join(
            segment.text.strip()
            for segment in segments
            if segment.text.strip()
        )
        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", None),
            duration_seconds=getattr(info, "duration", None),
        )

    def unload(self) -> None:
        self._model = None
        self._loaded_options = None

    def close(self) -> None:
        self.unload()

    def _ensure_model(self) -> _WhisperModel:
        options = (
            self.runtime_options()
            if self.runtime_options is not None
            else (self.device, self.compute_type)
        )
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

            device, compute_type = options
            self._model = self._model_factory(
                self.model_name,
                device=device,
                compute_type=compute_type,
            )
            self._loaded_options = options

        return self._model

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
