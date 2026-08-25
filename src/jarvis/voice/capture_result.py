"""Resultados observáveis de uma tentativa de captura de voz."""

from dataclasses import dataclass
from enum import Enum


class VoiceCaptureStatus(Enum):
    SUCCESS = "success"
    NO_SPEECH = "no_speech"
    TRANSCRIPTION_REJECTED = "transcription_rejected"
    USER_CANCELLED = "user_cancelled"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class VoiceCaptureResult:
    status: VoiceCaptureStatus
    spoke: bool = False
    repairable: bool = False
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status is VoiceCaptureStatus.SUCCESS
