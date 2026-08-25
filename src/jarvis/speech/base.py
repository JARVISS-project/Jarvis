from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    duration_seconds: float | None


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
