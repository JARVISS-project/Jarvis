from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    duration_seconds: float | None
    average_log_probability: float | None = None
    no_speech_probability: float | None = None
    language_probability: float | None = None
    duration_after_vad_seconds: float | None = None


class SpeechToText(ABC):
    @abstractmethod
    def transcribe(
        self,
        audio: Sequence[int],
        sample_rate: int,
    ) -> TranscriptionResult:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def transcribe_retry(
        self,
        audio: Sequence[int],
        sample_rate: int,
    ) -> TranscriptionResult:
        """Retry opcional sobre o mesmo áudio, sem nova captura."""
        return self.transcribe(audio, sample_rate)
