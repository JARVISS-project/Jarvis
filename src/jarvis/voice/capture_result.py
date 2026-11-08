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


class NoSpeechReason(Enum):
    NO_FRAMES = "no_frames_received"
    SILENT_FRAMES = "silent_frames"
    SPEECH_TOO_SHORT = "speech_below_minimum"
    PRE_SPEECH_TIMEOUT = "timeout_before_speech"
    EARLY_TERMINATION = "early_termination"
    DEVICE_ERROR = "device_error"
    CONSUMER_CONTENTION = "consumer_contention"
    CANCELLED = "cancelled"
    VAD_FAILURE = "vad_failure"
    EMPTY_TRANSCRIPTION = "valid_audio_empty_transcription"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VoiceCaptureResult:
    status: VoiceCaptureStatus
    spoke: bool = False
    repairable: bool = False
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status is VoiceCaptureStatus.SUCCESS
