from jarvis.speech.base import TranscriptionResult
from jarvis.speech.validator import (
    TranscriptionProfile,
    TranscriptionQuality,
    TranscriptionValidator,
)


def test_validator_rejects_empty_short_and_no_speech() -> None:
    validator = TranscriptionValidator()
    assert not validator.is_valid(
        TranscriptionResult("", "pt", 1.0), captured_samples=16000, sample_rate=16000
    )
    assert not validator.is_valid(
        TranscriptionResult("sim", "pt", 0.01), captured_samples=10, sample_rate=16000
    )
    assert not validator.is_valid(
        TranscriptionResult("sim", "pt", 1.0, no_speech_probability=0.9),
        captured_samples=16000,
        sample_rate=16000,
    )
    assert not validator.is_valid(
        TranscriptionResult(
            "sim", "pt", 1.0, duration_after_vad_seconds=0.05
        ),
        captured_samples=16000,
        sample_rate=16000,
    )


def test_quality_profiles_keep_short_followup_but_reject_general_command() -> None:
    validator = TranscriptionValidator()
    result = TranscriptionResult(
        "ela",
        "pt",
        0.2,
        average_log_probability=-0.5,
        no_speech_probability=0.1,
        duration_after_vad_seconds=0.2,
    )
    general = validator.validate(
        result,
        captured_samples=3200,
        sample_rate=16000,
        profile=TranscriptionProfile.GENERAL_COMMAND,
    )
    followup = validator.validate(
        result,
        captured_samples=3200,
        sample_rate=16000,
        profile=TranscriptionProfile.FOLLOWUP,
    )
    assert general.accepted is False
    assert followup.accepted is True


def test_confirmation_profile_accepts_acoustically_valid_short_yes() -> None:
    result = TranscriptionResult(
        "sim", "pt", 0.12, average_log_probability=-0.4,
        no_speech_probability=0.1, duration_after_vad_seconds=0.12,
    )
    validation = TranscriptionValidator().validate(
        result,
        captured_samples=2000,
        sample_rate=16000,
        profile=TranscriptionProfile.CONFIRMATION,
    )
    assert validation.accepted is True


def test_avg_logprob_alone_makes_real_speech_borderline_not_rejected() -> None:
    result = TranscriptionResult(
        "que horas são", "pt", 1.0,
        average_log_probability=-0.9,
        no_speech_probability=0.01,
        duration_after_vad_seconds=0.9,
    )
    validation = TranscriptionValidator().validate(
        result,
        captured_samples=16000,
        sample_rate=16000,
        profile=TranscriptionProfile.GENERAL_COMMAND,
    )
    assert validation.quality is TranscriptionQuality.BORDERLINE


def test_quality_has_accepted_borderline_and_rejected_states() -> None:
    validator = TranscriptionValidator()
    accepted = TranscriptionResult(
        "abra a segunda", "pt", 0.8, average_log_probability=-0.3,
        no_speech_probability=0.05, duration_after_vad_seconds=0.7,
    )
    rejected = TranscriptionResult(
        "ruído", "pt", 0.8, average_log_probability=-0.1,
        no_speech_probability=0.95, duration_after_vad_seconds=0.7,
    )
    assert validator.validate(
        accepted, captured_samples=12800, sample_rate=16000,
        profile=TranscriptionProfile.GENERAL_COMMAND,
    ).quality is TranscriptionQuality.ACCEPTED
    assert validator.validate(
        rejected, captured_samples=12800, sample_rate=16000,
        profile=TranscriptionProfile.GENERAL_COMMAND,
    ).quality is TranscriptionQuality.REJECTED


def test_stable_low_confidence_consensus_remains_borderline() -> None:
    validator = TranscriptionValidator()
    first = TranscriptionResult(
        "e audacao", "pt", 0.8, average_log_probability=-1.02,
        no_speech_probability=0.05, duration_after_vad_seconds=0.7,
    )
    second = TranscriptionResult(
        "e audacao", "pt", 0.8, average_log_probability=-1.01,
        no_speech_probability=0.04, duration_after_vad_seconds=0.7,
    )
    second_validation = validator.validate(
        second, captured_samples=12800, sample_rate=16000,
        profile=TranscriptionProfile.GENERAL_COMMAND,
    )
    _, consensus = validator.consensus(first, second, second_validation)
    assert consensus.quality is TranscriptionQuality.BORDERLINE
    assert consensus.reason == (
        "stable_borderline_average_log_probability"
    )
    assert consensus.stability == 1.0


def test_stable_near_threshold_general_command_is_accepted() -> None:
    validator = TranscriptionValidator()
    first = TranscriptionResult(
        "que horas são", "pt", 1.0,
        average_log_probability=-0.581,
        no_speech_probability=0.048,
        duration_after_vad_seconds=0.872,
    )
    second = TranscriptionResult(
        "Que horas são?", "pt", 1.0,
        average_log_probability=-0.579,
        no_speech_probability=0.046,
        duration_after_vad_seconds=0.872,
    )
    second_validation = validator.validate(
        second,
        captured_samples=28160,
        sample_rate=16000,
        profile=TranscriptionProfile.GENERAL_COMMAND,
    )

    _, consensus = validator.consensus(
        first,
        second,
        second_validation,
        profile=TranscriptionProfile.GENERAL_COMMAND,
    )

    assert consensus.quality is TranscriptionQuality.ACCEPTED
    assert consensus.reason == "stable_near_threshold_consensus"
    assert consensus.stability == 1.0


def test_divergent_consensus_remains_borderline_without_clear_improvement() -> None:
    validator = TranscriptionValidator()
    first = TranscriptionResult(
        "que adesão", "pt", 1.0, average_log_probability=-0.61,
        no_speech_probability=0.004, duration_after_vad_seconds=1.0,
    )
    second = TranscriptionResult(
        "que horas são", "pt", 1.0, average_log_probability=-0.60,
        no_speech_probability=0.003, duration_after_vad_seconds=1.0,
    )
    validation = validator.validate(
        second, captured_samples=16000, sample_rate=16000,
        profile=TranscriptionProfile.GENERAL_COMMAND,
    )
    _, consensus = validator.consensus(first, second, validation)
    assert consensus.quality is TranscriptionQuality.BORDERLINE
