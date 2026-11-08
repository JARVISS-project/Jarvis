import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarvis.speech.base import TranscriptionResult
from jarvis.speech.metrics import (
    SpeechRecognitionMetricsRecorder,
    summarize_speech_metrics,
)
from jarvis.speech.quality import (
    TranscriptionProfile,
    TranscriptionQuality,
    TranscriptionValidation,
)
from jarvis.speech.validator import TranscriptionValidator
from jarvis.voice.transcription_coordinator import TranscriptionCoordinator


def test_recorder_persists_metadata_without_transcript(tmp_path: Path) -> None:
    path = tmp_path / "metrics.jsonl"
    recorder = SpeechRecognitionMetricsRecorder(
        path,
        now=lambda: datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc),
    )
    result = TranscriptionResult(
        text="segredo que não deve ir para telemetria",
        language="pt",
        duration_seconds=1.2,
        average_log_probability=-0.21,
        no_speech_probability=0.03,
        language_probability=0.99,
        duration_after_vad_seconds=0.8,
    )
    validation = TranscriptionValidation(
        TranscriptionQuality.ACCEPTED,
        "accepted",
    )

    assert recorder.record(
        result=result,
        validation=validation,
        profile=TranscriptionProfile.GENERAL_COMMAND,
        captured_samples=16000,
        sample_rate=16000,
        elapsed_seconds=0.25,
        retry_used=False,
    )

    raw = path.read_text(encoding="utf-8")
    assert "segredo" not in raw
    payload = json.loads(raw)
    assert "text" not in payload
    assert payload["quality"] == "accepted"
    assert payload["captured_duration_ms"] == 1000
    assert payload["speech_duration_ms"] == 800
    assert payload["stt_latency_ms"] == 250


def test_summary_counts_quality_retry_and_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "metrics.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"quality":"accepted","reason":"accepted","retry_used":false}',
                '{"quality":"rejected","reason":"empty_text","retry_used":true}',
                "{broken",
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_speech_metrics(path)

    assert summary.total == 2
    assert summary.accepted == 1
    assert summary.rejected == 1
    assert summary.borderline == 0
    assert summary.retries == 1
    assert summary.malformed_lines == 1
    assert summary.reasons == {"accepted": 1, "empty_text": 1}


class RetrySpeechToText:
    def __init__(self) -> None:
        self.retry_calls = 0

    def transcribe(self, audio, sample_rate):
        return TranscriptionResult(
            "abra o github",
            "pt",
            1.0,
            average_log_probability=-0.8,
            no_speech_probability=0.05,
            duration_after_vad_seconds=0.7,
        )

    def transcribe_retry(self, audio, sample_rate):
        self.retry_calls += 1
        return TranscriptionResult(
            "abra o github",
            "pt",
            1.0,
            average_log_probability=-0.3,
            no_speech_probability=0.02,
            duration_after_vad_seconds=0.7,
        )


def test_coordinator_records_final_consensus_with_retry(tmp_path: Path) -> None:
    path = tmp_path / "metrics.jsonl"
    recorder = SpeechRecognitionMetricsRecorder(path)
    clock = iter((10.0, 10.25))
    stt = RetrySpeechToText()
    coordinator = TranscriptionCoordinator(
        stt,
        sample_rate=16000,
        validator=TranscriptionValidator(),
        monotonic_clock=lambda: next(clock),
        debug=lambda _message: None,
        cancel_max_no_speech_probability=0.35,
        cancel_min_average_log_probability=-0.6,
        cancel_min_speech_duration_seconds=0.2,
        metrics_recorder=recorder,
    )

    _result, validation, elapsed = coordinator.transcribe_and_validate(
        [100] * 16000,
        TranscriptionProfile.GENERAL_COMMAND,
    )

    assert stt.retry_calls == 1
    assert elapsed == 0.25
    assert validation.accepted
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["retry_used"] is True
    assert payload["reason"] == "stable_consensus"
    assert payload["stability"] == 1.0


def test_recorder_fails_safely_when_path_is_directory(tmp_path: Path) -> None:
    recorder = SpeechRecognitionMetricsRecorder(tmp_path)
    result = TranscriptionResult("teste", "pt", 1.0)
    validation = TranscriptionValidation(
        TranscriptionQuality.ACCEPTED,
        "accepted",
    )

    assert recorder.record(
        result=result,
        validation=validation,
        profile=TranscriptionProfile.GENERAL_COMMAND,
        captured_samples=16000,
        sample_rate=16000,
        elapsed_seconds=0.1,
        retry_used=False,
    ) is False


class RaisingMetricsRecorder:
    def record(self, **_kwargs):
        raise RuntimeError("disk failure")


def test_coordinator_metrics_failure_is_nonfatal() -> None:
    clock = iter((20.0, 20.2))
    messages: list[str] = []
    coordinator = TranscriptionCoordinator(
        RetrySpeechToText(),
        sample_rate=16000,
        validator=TranscriptionValidator(),
        monotonic_clock=lambda: next(clock),
        debug=messages.append,
        cancel_max_no_speech_probability=0.35,
        cancel_min_average_log_probability=-0.6,
        cancel_min_speech_duration_seconds=0.2,
        metrics_recorder=RaisingMetricsRecorder(),
    )

    _result, validation, elapsed = coordinator.transcribe_and_validate(
        [100] * 16000,
        TranscriptionProfile.GENERAL_COMMAND,
    )

    assert validation.accepted
    assert elapsed == pytest.approx(0.2)
    assert "stt metrics write=false" in messages
