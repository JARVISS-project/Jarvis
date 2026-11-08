from __future__ import annotations

from collections.abc import Callable

from jarvis.speech.base import SpeechToText, TranscriptionResult
from jarvis.speech.ptbr_fallback import PtBrFallback
from jarvis.speech.metrics import SpeechRecognitionMetricsRecorder
from jarvis.speech.validator import (
    TranscriptionProfile,
    TranscriptionQuality,
    TranscriptionValidation,
    TranscriptionValidator,
)


class TranscriptionCoordinator:
    """Owns STT validation, consensus retry and transcription diagnostics."""

    def __init__(
        self,
        speech_to_text: SpeechToText,
        *,
        sample_rate: int,
        validator: TranscriptionValidator | None,
        monotonic_clock: Callable[[], float],
        debug: Callable[[str], None],
        cancel_max_no_speech_probability: float,
        cancel_min_average_log_probability: float,
        cancel_min_speech_duration_seconds: float,
        metrics_recorder: SpeechRecognitionMetricsRecorder | None = None,
        fallback: PtBrFallback | None = None,
        fallback_allowed: Callable[[], bool] | None = None,
        trace: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self.speech_to_text = speech_to_text
        self.sample_rate = sample_rate
        self.validator = validator
        self.monotonic_clock = monotonic_clock
        self.debug = debug
        self.cancel_max_no_speech_probability = cancel_max_no_speech_probability
        self.cancel_min_average_log_probability = cancel_min_average_log_probability
        self.cancel_min_speech_duration_seconds = cancel_min_speech_duration_seconds
        self.metrics_recorder = metrics_recorder
        self.fallback = fallback
        self.fallback_allowed = fallback_allowed or (lambda: True)
        self.trace = trace

    def _trace(self, event: str, **fields: object) -> None:
        if self.trace is not None:
            self.trace(event, fields)

    def is_reliable_control_transcription(self, result: object) -> bool:
        if getattr(result, "_jarvis_stt_source", None) == "ptbr_fallback":
            return False
        no_speech = getattr(result, "no_speech_probability", None)
        if (
            no_speech is not None
            and no_speech > self.cancel_max_no_speech_probability
        ):
            return False
        average_log_probability = getattr(
            result, "average_log_probability", None
        )
        if (
            average_log_probability is not None
            and average_log_probability
            < self.cancel_min_average_log_probability
        ):
            return False
        speech_duration = getattr(result, "duration_after_vad_seconds", None)
        return not (
            speech_duration is not None
            and speech_duration < self.cancel_min_speech_duration_seconds
        )

    def transcribe_and_validate(
        self,
        audio: list[int],
        profile: TranscriptionProfile,
    ) -> tuple[object, TranscriptionValidation, float]:
        started = self.monotonic_clock()
        retry_used = False
        result = self.speech_to_text.transcribe(
            audio, sample_rate=self.sample_rate
        )
        validation = self.validate(result, audio, profile)
        self._trace(
            "stt_initial", text=result.text,
            quality=validation.quality.name, reason=validation.reason,
            average_log_probability=getattr(result, "average_log_probability", None),
            no_speech_probability=getattr(result, "no_speech_probability", None),
        )
        self._trace(
            "stt_retry_decision",
            requested=validation.quality is TranscriptionQuality.BORDERLINE,
            reason=validation.reason,
        )
        if validation.quality is TranscriptionQuality.BORDERLINE:
            retry_used = True
            self.debug("quality result=BORDERLINE")
            self.debug("stt consensus retry=1 same_audio=true")
            first = result
            second = self.speech_to_text.transcribe_retry(
                audio, sample_rate=self.sample_rate
            )
            second_validation = self.validate(second, audio, profile)
            self._trace(
                "stt_retry", text=second.text,
                quality=second_validation.quality.name,
                reason=second_validation.reason,
                average_log_probability=getattr(second, "average_log_probability", None),
                no_speech_probability=getattr(second, "no_speech_probability", None),
            )
            if self.validator is not None:
                result, validation = self.validator.consensus(
                    first,
                    second,
                    second_validation,
                    profile=profile,
                )
                if validation.stability is not None:
                    self._trace("stt_stability", value=validation.stability)
                    self.debug(
                        "transcript stability="
                        f"{validation.stability:.3f}"
                    )
        fallback_requested = self._should_try_fallback(result, validation, profile)
        self._trace(
            "fallback_decision", requested=fallback_requested,
            reason=validation.reason,
        )
        if fallback_requested:
            fallback = self.fallback
            if fallback is not None:
                self.debug("stt fallback=ptbr_wav2vec2 requested")
                self._trace("fallback_requested", model="ptbr_wav2vec2")
                fallback_result = fallback.transcribe(audio, self.sample_rate)
                if fallback_result is not None and fallback.accepts(
                    fallback_result
                ):
                    result = TranscriptionResult(
                        text=fallback_result.text,
                        language="pt",
                        duration_seconds=fallback_result.duration_seconds,
                        duration_after_vad_seconds=(
                            fallback_result.duration_seconds
                        ),
                    )
                    setattr(result, "_jarvis_stt_source", "ptbr_fallback")
                    validation = TranscriptionValidation(
                        TranscriptionQuality.ACCEPTED,
                        "ptbr_fallback_high_confidence",
                    )
                    self.debug(
                        "stt fallback=ptbr_wav2vec2 accepted "
                        f"confidence={fallback_result.confidence:.3f} "
                        f"margin={fallback_result.margin:.3f}"
                    )
                    self._trace(
                        "fallback_accepted", text=fallback_result.text,
                        confidence=fallback_result.confidence,
                        margin=fallback_result.margin,
                    )
                elif fallback_result is not None:
                    self.debug(
                        "stt fallback=ptbr_wav2vec2 rejected "
                        f"confidence={fallback_result.confidence:.3f} "
                        f"margin={fallback_result.margin:.3f}"
                    )
                    self._trace(
                        "fallback_rejected", text=fallback_result.text,
                        confidence=fallback_result.confidence,
                        margin=fallback_result.margin,
                    )
                else:
                    self.debug("stt fallback=ptbr_wav2vec2 unavailable")
                    self._trace("fallback_unavailable")
        self._trace(
            "stt_selected", text=result.text,
            source=getattr(result, "_jarvis_stt_source", "faster_whisper"),
            quality=validation.quality.name, reason=validation.reason,
        )
        elapsed = self.monotonic_clock() - started
        metrics_recorder = self.metrics_recorder
        if metrics_recorder is not None:
            try:
                stored = metrics_recorder.record(
                    result=result,
                    validation=validation,
                    profile=profile,
                    captured_samples=len(audio),
                    sample_rate=self.sample_rate,
                    elapsed_seconds=elapsed,
                    retry_used=retry_used,
                )
            except Exception:
                stored = False
            if not stored:
                self.debug("stt metrics write=false")
        return result, validation, elapsed

    def _should_try_fallback(
        self,
        result: object,
        validation: TranscriptionValidation,
        profile: TranscriptionProfile,
    ) -> bool:
        if (
            self.fallback is None
            or not self.fallback_allowed()
            or validation.quality is not TranscriptionQuality.BORDERLINE
            or profile
            not in {
                TranscriptionProfile.GENERAL_COMMAND,
                TranscriptionProfile.FOLLOWUP,
            }
            or validation.reason
            not in {
                "stable_borderline_average_log_probability",
                "unstable_consensus",
            }
        ):
            return False
        no_speech = getattr(result, "no_speech_probability", None)
        speech_duration = getattr(
            result, "duration_after_vad_seconds", None
        )
        return (
            isinstance(no_speech, (int, float))
            and no_speech <= 0.15
            and isinstance(speech_duration, (int, float))
            and speech_duration >= 0.50
        )

    def validate(
        self,
        result: object,
        audio: list[int],
        profile: TranscriptionProfile,
    ) -> TranscriptionValidation:
        if self.validator is None:
            accepted = bool(getattr(result, "text", "").strip())
            return TranscriptionValidation(
                (
                    TranscriptionQuality.ACCEPTED
                    if accepted
                    else TranscriptionQuality.REJECTED
                ),
                "accepted" if accepted else "empty_text",
            )
        return self.validator.validate(
            result,  # type: ignore[arg-type]
            captured_samples=len(audio),
            sample_rate=self.sample_rate,
            profile=profile,
        )

    def debug_rejection(
        self,
        result: object,
        profile: TranscriptionProfile | None = None,
        reason: str = "rejected",
    ) -> None:
        metadata = []
        for label, attribute in (
            ("duration", "duration_seconds"),
            ("speech_duration", "duration_after_vad_seconds"),
            ("avg_logprob", "average_log_probability"),
            ("no_speech", "no_speech_probability"),
        ):
            value = getattr(result, attribute, None)
            if value is not None:
                metadata.append(f"{label}={value:.3f}")
        suffix = f" {' '.join(metadata)}" if metadata else ""
        quality = (
            "BORDERLINE"
            if reason == "unstable_consensus"
            else "REJECTED"
        )
        profile_text = (
            f" quality profile={profile.name}"
            if profile is not None
            else ""
        )
        self.debug(
            f"quality result={quality} reason={reason}"
            f"{profile_text}{suffix}"
        )

    def debug_accepted(
        self,
        result: object,
        profile: TranscriptionProfile,
    ) -> None:
        metadata = []
        for label, attribute in (
            ("speech_duration", "duration_after_vad_seconds"),
            ("avg_logprob", "average_log_probability"),
            ("no_speech", "no_speech_probability"),
        ):
            value = getattr(result, attribute, None)
            if value is not None:
                metadata.append(f"{label}={value:.3f}")
        suffix = f" {' '.join(metadata)}" if metadata else ""
        self.debug(
            f"quality profile={profile.name} result=ACCEPTED{suffix}"
        )
