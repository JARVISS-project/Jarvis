"""Qualidade composta e consenso de transcrições locais."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
import re
import unicodedata

from jarvis.speech.base import TranscriptionResult


class TranscriptionProfile(Enum):
    GENERAL_COMMAND = "general_command"
    FOLLOWUP = "followup"
    CONFIRMATION = "confirmation"


class TranscriptionQuality(Enum):
    ACCEPTED = "accepted"
    BORDERLINE = "borderline"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TranscriptionValidation:
    quality: TranscriptionQuality
    reason: str = "accepted"
    stability: float | None = None

    @property
    def accepted(self) -> bool:
        return self.quality is TranscriptionQuality.ACCEPTED

    @property
    def retryable(self) -> bool:
        return self.quality is TranscriptionQuality.BORDERLINE


_PROFILE_LIMITS = {
    TranscriptionProfile.GENERAL_COMMAND: (0.15, 0.35, 0.45, -0.55),
    TranscriptionProfile.FOLLOWUP: (0.10, 0.15, 0.55, -0.75),
    TranscriptionProfile.CONFIRMATION: (0.08, 0.10, 0.50, -0.70),
}


class TranscriptionValidator:
    """Classifica sinal e metadados; nenhuma métrica isolada rejeita."""

    def __init__(
        self,
        minimum_audio_seconds: float = 0.15,
        maximum_no_speech_probability: float = 0.6,
        minimum_average_log_probability: float = -1.0,
    ) -> None:
        self.minimum_audio_seconds = minimum_audio_seconds
        self.maximum_no_speech_probability = maximum_no_speech_probability
        self.minimum_average_log_probability = minimum_average_log_probability

    def is_valid(
        self,
        result: TranscriptionResult,
        *,
        captured_samples: int,
        sample_rate: int,
    ) -> bool:
        return self.validate(
            result, captured_samples=captured_samples, sample_rate=sample_rate
        ).accepted

    def validate(
        self,
        result: TranscriptionResult,
        *,
        captured_samples: int,
        sample_rate: int,
        profile: TranscriptionProfile | None = None,
    ) -> TranscriptionValidation:
        if profile is None:
            hard_minimum = preferred_minimum = self.minimum_audio_seconds
            max_no_speech = self.maximum_no_speech_probability
            comfortable_logprob = self.minimum_average_log_probability
        else:
            (
                hard_minimum,
                preferred_minimum,
                max_no_speech,
                comfortable_logprob,
            ) = _PROFILE_LIMITS[profile]
        if sample_rate <= 0 or captured_samples <= 0:
            return TranscriptionValidation(
                TranscriptionQuality.REJECTED, "invalid_audio"
            )
        if not result.text.strip():
            return TranscriptionValidation(
                TranscriptionQuality.REJECTED, "empty_text"
            )
        captured_duration = captured_samples / sample_rate
        useful_duration = (
            result.duration_after_vad_seconds
            if result.duration_after_vad_seconds is not None
            else captured_duration
        )
        if useful_duration < hard_minimum:
            return TranscriptionValidation(
                TranscriptionQuality.REJECTED, "speech_too_short"
            )
        no_speech = result.no_speech_probability
        if no_speech is not None and no_speech >= 0.80:
            return TranscriptionValidation(
                TranscriptionQuality.REJECTED, "no_speech_probability"
            )
        reasons = []
        if useful_duration < preferred_minimum:
            reasons.append("short_speech")
        if no_speech is not None and no_speech > max_no_speech:
            reasons.append("no_speech_probability")
        logprob = result.average_log_probability
        if logprob is not None and logprob < comfortable_logprob:
            reasons.append("average_log_probability")
        if reasons:
            return TranscriptionValidation(
                TranscriptionQuality.BORDERLINE, "+".join(reasons)
            )
        return TranscriptionValidation(TranscriptionQuality.ACCEPTED)

    def consensus(
        self,
        first: TranscriptionResult,
        second: TranscriptionResult,
        second_validation: TranscriptionValidation,
        profile: TranscriptionProfile | None = None,
    ) -> tuple[TranscriptionResult, TranscriptionValidation]:
        stability = SequenceMatcher(
            None, self._normalize(first.text), self._normalize(second.text)
        ).ratio()
        if second_validation.quality is TranscriptionQuality.REJECTED:
            return second, TranscriptionValidation(
                TranscriptionQuality.REJECTED,
                second_validation.reason,
                stability,
            )
        if stability >= 0.82:
            better = self._better(first, second)
            if second_validation.accepted:
                return better, TranscriptionValidation(
                    TranscriptionQuality.ACCEPTED,
                    "stable_consensus",
                    stability,
                )
            if (
                stability >= 0.97
                and profile is TranscriptionProfile.GENERAL_COMMAND
                and second_validation.reason == "average_log_probability"
                and self._is_near_threshold_speech(first, profile)
                and self._is_near_threshold_speech(second, profile)
            ):
                return better, TranscriptionValidation(
                    TranscriptionQuality.ACCEPTED,
                    "stable_near_threshold_consensus",
                    stability,
                )
            return better, TranscriptionValidation(
                TranscriptionQuality.BORDERLINE,
                "stable_borderline_" + second_validation.reason,
                stability,
            )
        if self._clearly_better(second, first) and second_validation.accepted:
            return second, TranscriptionValidation(
                TranscriptionQuality.ACCEPTED,
                "improved_second_decode",
                stability,
            )
        return second, TranscriptionValidation(
            TranscriptionQuality.BORDERLINE,
            "unstable_consensus",
            stability,
        )

    @staticmethod
    def _is_near_threshold_speech(
        result: TranscriptionResult,
        profile: TranscriptionProfile,
    ) -> bool:
        (
            _hard_minimum,
            preferred_minimum,
            _max_no_speech,
            comfortable_logprob,
        ) = _PROFILE_LIMITS[profile]
        logprob = result.average_log_probability
        no_speech = result.no_speech_probability
        speech_duration = result.duration_after_vad_seconds
        return (
            logprob is not None
            and logprob >= comfortable_logprob - 0.10
            and (no_speech is None or no_speech <= 0.15)
            and speech_duration is not None
            and speech_duration >= max(0.50, preferred_minimum)
        )

    @staticmethod
    def _better(
        first: TranscriptionResult,
        second: TranscriptionResult,
    ) -> TranscriptionResult:
        first_score = first.average_log_probability
        second_score = second.average_log_probability
        if first_score is None:
            return second
        if second_score is None:
            return first
        return second if second_score > first_score else first

    @staticmethod
    def _clearly_better(
        candidate: TranscriptionResult,
        original: TranscriptionResult,
    ) -> bool:
        log_improved = (
            candidate.average_log_probability is not None
            and original.average_log_probability is not None
            and candidate.average_log_probability
            >= original.average_log_probability + 0.20
        )
        speech_improved = (
            candidate.no_speech_probability is not None
            and original.no_speech_probability is not None
            and candidate.no_speech_probability
            <= original.no_speech_probability - 0.15
        )
        return log_improved or speech_improved

    @staticmethod
    def _normalize(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text.casefold())
        unaccented = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^\w\s]", " ", unaccented).split())
