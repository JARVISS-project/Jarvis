from array import array
import json
from pathlib import Path
import wave

import pytest

from scripts import benchmark_wake_word as benchmark_script

from jarvis.activation.benchmark import (
    WakeBenchmarkResult,
    WakeBenchmarkSummary,
    WakeContinuousBenchmarkResult,
    WakePhaseBenchmarkResult,
    WakePhaseBenchmarkSummary,
    benchmark_continuous_negative_stream,
    benchmark_wake_clip,
    benchmark_wake_clip_phases,
    benchmark_wake_dataset,
    read_pcm16_mono_wav,
)


class FakeWakeDetector:
    frame_length = 1280
    sample_rate = 16000

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold
        self.last_score = 0.0
        self.reset_calls = 0

    def process(self, frame) -> bool:
        self.last_score = max(abs(sample) for sample in frame) / 10000
        return self.last_score >= self.threshold

    def reset(self) -> None:
        self.reset_calls += 1
        self.last_score = 0.0


def write_wav(
    path: Path,
    samples: list[int],
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> None:
    payload = array("h", samples).tobytes()
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(payload)


def test_benchmark_summarizes_false_rejects_and_false_accepts(
    tmp_path: Path,
) -> None:
    positive_hit = tmp_path / "positive_hit.wav"
    positive_miss = tmp_path / "positive_miss.wav"
    negative_clean = tmp_path / "negative_clean.wav"
    negative_false_accept = tmp_path / "negative_false_accept.wav"
    write_wav(positive_hit, [9000] * 1280)
    write_wav(positive_miss, [1000] * 1280)
    write_wav(negative_clean, [500] * 1280)
    write_wav(negative_false_accept, [8000] * 1280)

    detector = FakeWakeDetector()
    summary = benchmark_wake_dataset(
        detector,
        positive_paths=[positive_hit, positive_miss],
        negative_paths=[negative_clean, negative_false_accept],
    )

    assert summary.positives == 2
    assert summary.negatives == 2
    assert summary.true_positives == 1
    assert summary.false_rejects == 1
    assert summary.true_negatives == 1
    assert summary.false_accepts == 1
    assert summary.false_reject_rate == pytest.approx(0.5)
    assert summary.false_accept_rate == pytest.approx(0.5)
    assert detector.reset_calls == 8


def test_clip_reports_peak_score_activation_frame_and_padding(
    tmp_path: Path,
) -> None:
    clip = tmp_path / "delayed.wav"
    write_wav(clip, [1000] * 1280 + [9000] * 20)

    result = benchmark_wake_clip(
        FakeWakeDetector(),
        clip,
        expected_activation=True,
    )
    assert result.detected is True
    assert result.peak_score == pytest.approx(0.9)
    assert result.activation_frame == 1
    assert result.processed_frames == 2
    assert result.duration_seconds == pytest.approx(1300 / 16000)


@pytest.mark.parametrize(
    ("sample_rate", "channels", "message"),
    [
        (8000, 1, "esperado 16000 Hz"),
        (16000, 2, "WAV deve ser mono"),
    ],
)
def test_wav_validation_rejects_incompatible_audio(
    tmp_path: Path,
    sample_rate: int,
    channels: int,
    message: str,
) -> None:
    clip = tmp_path / "invalid.wav"
    samples = [100] * (1280 * channels)
    write_wav(
        clip,
        samples,
        sample_rate=sample_rate,
        channels=channels,
    )

    with pytest.raises(ValueError, match=message):
        read_pcm16_mono_wav(clip, sample_rate=16000)


def test_manifest_selects_only_explicitly_labeled_clips(
    tmp_path: Path,
) -> None:
    positive = tmp_path / "positive.wav"
    negative = tmp_path / "negative.wav"
    write_wav(positive, [1000] * 1280)
    write_wav(negative, [500] * 1280)
    manifest = tmp_path / "capture_manifest.jsonl"
    manifest.write_text(
        "\n".join(
            (
                json.dumps({"path": str(positive), "label": "positive"}),
                json.dumps({"path": str(negative), "label": "negative"}),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    positives, negatives = benchmark_script._manifest_files([manifest])

    assert positives == [positive]
    assert negatives == [negative]


def test_manifest_rejects_unknown_label(tmp_path: Path) -> None:
    clip = tmp_path / "clip.wav"
    write_wav(clip, [1000] * 1280)
    manifest = tmp_path / "capture_manifest.jsonl"
    manifest.write_text(
        json.dumps({"path": str(clip), "label": "maybe"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="label inválido"):
        benchmark_script._manifest_files([manifest])


def test_benchmark_parser_accepts_verifier_overrides() -> None:
    args = benchmark_script.build_parser().parse_args(
        [
            "--positive",
            "positive.wav",
            "--verifier-path",
            "verifier.npz",
            "--verifier-candidate-threshold",
            "0.2",
            "--verifier-threshold",
            "0.7",
            "--verifier-strong-threshold",
            "0.85",
            "--no-verifier",
        ]
    )

    assert args.verifier_path == Path("verifier.npz")
    assert args.verifier_candidate_threshold == pytest.approx(0.2)
    assert args.verifier_threshold == pytest.approx(0.7)
    assert args.verifier_strong_threshold == pytest.approx(0.85)
    assert args.no_verifier is True



class RecordingStreamDetector:
    frame_length = 4
    sample_rate = 8

    def __init__(self) -> None:
        self.frames: list[list[int]] = []
        self.last_score = 0.0
        self.reset_calls = 0

    def process(self, frame) -> bool:
        rendered = list(frame)
        self.frames.append(rendered)
        self.last_score = max(abs(sample) for sample in rendered) / 10000
        return False

    def reset(self) -> None:
        self.reset_calls += 1
        self.last_score = 0.0


def test_continuous_negative_stream_carries_samples_across_clip_boundary(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    write_wav(first, [1, 2, 3], sample_rate=8)
    write_wav(second, [4, 5, 6, 7, 8], sample_rate=8)
    detector = RecordingStreamDetector()

    result = benchmark_continuous_negative_stream(
        detector,
        negative_paths=[first, second],
    )

    assert detector.frames == [[1, 2, 3, 4], [5, 6, 7, 8]]
    assert result.clip_count == 2
    assert result.repeats == 1
    assert result.processed_frames == 2
    assert result.duration_seconds == pytest.approx(1.0)
    assert result.activations == 0
    assert detector.reset_calls == 2


def test_continuous_negative_stream_inserts_requested_silence(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    write_wav(first, [1, 2, 3], sample_rate=8)
    write_wav(second, [4], sample_rate=8)
    detector = RecordingStreamDetector()

    result = benchmark_continuous_negative_stream(
        detector,
        negative_paths=[first, second],
        silence_seconds=0.5,
    )

    assert detector.frames == [[1, 2, 3, 0], [0, 0, 0, 4]]
    assert result.duration_seconds == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("repeats", "silence_seconds", "message"),
    [
        (0, 0.0, "repeats"),
        (1, -0.1, "silence_seconds"),
    ],
)
def test_continuous_negative_stream_rejects_invalid_options(
    tmp_path: Path,
    repeats: int,
    silence_seconds: float,
    message: str,
) -> None:
    clip = tmp_path / "negative.wav"
    write_wav(clip, [0] * 8, sample_rate=8)

    with pytest.raises(ValueError, match=message):
        benchmark_continuous_negative_stream(
            RecordingStreamDetector(),
            negative_paths=[clip],
            repeats=repeats,
            silence_seconds=silence_seconds,
        )


def test_continuous_negative_stream_requires_negative_clips() -> None:
    with pytest.raises(ValueError, match="negative_paths"):
        benchmark_continuous_negative_stream(
            RecordingStreamDetector(),
            negative_paths=[],
        )



def test_benchmark_parser_accepts_continuous_negative_options() -> None:
    args = benchmark_script.build_parser().parse_args(
        [
            "--negative",
            "negative.wav",
            "--continuous-negatives",
            "--continuous-repeats",
            "5",
            "--continuous-silence-seconds",
            "0.5",
        ]
    )

    assert args.continuous_negatives is True
    assert args.continuous_repeats == 5
    assert args.continuous_silence_seconds == pytest.approx(0.5)


class PhaseSensitiveDetector:
    frame_length = 4
    sample_rate = 8

    def __init__(self) -> None:
        self.last_score = 0.0
        self.reset_calls = 0
        self.frames: list[list[int]] = []

    def process(self, frame) -> bool:
        rendered = list(frame)
        self.frames.append(rendered)
        detected = rendered == [0, 9000, 9000, 9000]
        self.last_score = 1.0 if detected else 0.0
        return detected

    def reset(self) -> None:
        self.reset_calls += 1
        self.last_score = 0.0


def test_phase_aware_benchmark_varies_onset_inside_detector_frame(
    tmp_path: Path,
) -> None:
    clip = tmp_path / "phase.wav"
    write_wav(
        clip,
        [9000, 9000, 9000, 9000],
        sample_rate=8,
    )
    detector = PhaseSensitiveDetector()

    results = benchmark_wake_clip_phases(
        detector,
        clip,
        expected_activation=False,
        phase_step_ms=125.0,
        leading_silence_seconds=0.5,
        trailing_silence_seconds=0.0,
    )

    assert [result.onset_phase_samples for result in results] == [
        0,
        1,
        2,
        3,
    ]
    assert [result.onset_phase_ms for result in results] == [
        pytest.approx(0.0),
        pytest.approx(125.0),
        pytest.approx(250.0),
        pytest.approx(375.0),
    ]
    assert [result.detected for result in results] == [
        False,
        True,
        False,
        False,
    ]
    assert detector.reset_calls == 8
    assert detector.frames == [
        [0, 0, 0, 0],
        [9000, 9000, 9000, 9000],
        [0, 0, 0, 0],
        [0, 9000, 9000, 9000],
        [9000, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 9000, 9000],
        [9000, 9000, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 9000],
        [9000, 9000, 9000, 0],
    ]


@pytest.mark.parametrize(
    (
        "phase_step_ms",
        "leading_silence_seconds",
        "trailing_silence_seconds",
        "message",
    ),
    [
        (0.0, 2.0, 0.24, "phase_step_ms"),
        (600.0, 2.0, 0.24, "phase_step_ms"),
        (10.0, -0.1, 0.24, "leading_silence_seconds"),
        (10.0, 2.0, -0.1, "trailing_silence_seconds"),
    ],
)
def test_phase_aware_benchmark_rejects_invalid_options(
    tmp_path: Path,
    phase_step_ms: float,
    leading_silence_seconds: float,
    trailing_silence_seconds: float,
    message: str,
) -> None:
    clip = tmp_path / "phase-invalid.wav"
    write_wav(clip, [0] * 8, sample_rate=8)

    with pytest.raises(ValueError, match=message):
        benchmark_wake_clip_phases(
            PhaseSensitiveDetector(),
            clip,
            expected_activation=False,
            phase_step_ms=phase_step_ms,
            leading_silence_seconds=leading_silence_seconds,
            trailing_silence_seconds=trailing_silence_seconds,
        )


def test_benchmark_parser_accepts_phase_aware_options() -> None:
    args = benchmark_script.build_parser().parse_args(
        [
            "--negative",
            "negative.wav",
            "--phase-aware",
            "--phase-step-ms",
            "10",
            "--phase-silence-seconds",
            "2",
            "--phase-tail-silence-seconds",
            "0.24",
        ]
    )

    assert args.phase_aware is True
    assert args.phase_step_ms == pytest.approx(10.0)
    assert args.phase_silence_seconds == pytest.approx(2.0)
    assert args.phase_tail_silence_seconds == pytest.approx(0.24)


def _summary_result(
    *,
    expected: bool,
    detected: bool,
) -> WakeBenchmarkResult:
    return WakeBenchmarkResult(
        path=Path("synthetic.wav"),
        expected_activation=expected,
        detected=detected,
        peak_score=1.0 if detected else 0.0,
        activation_frame=0 if detected else None,
        processed_frames=1,
        duration_seconds=0.08,
    )


def _phase_result(
    *,
    expected: bool,
    detected: bool,
) -> WakePhaseBenchmarkResult:
    return WakePhaseBenchmarkResult(
        path=Path("synthetic.wav"),
        expected_activation=expected,
        onset_phase_samples=0,
        onset_phase_ms=0.0,
        detected=detected,
        peak_score=1.0 if detected else 0.0,
        activation_frame=0 if detected else None,
        processed_frames=1,
        clip_duration_seconds=0.08,
    )


def test_acceptance_gates_fail_on_false_accepts_and_continuous_activation() -> None:
    base = WakeBenchmarkSummary(
        (
            _summary_result(expected=True, detected=True),
            _summary_result(expected=False, detected=True),
        )
    )
    phase = WakePhaseBenchmarkSummary(
        (
            _phase_result(expected=True, detected=True),
            _phase_result(expected=False, detected=False),
        )
    )
    continuous = WakeContinuousBenchmarkResult(
        clip_count=1,
        repeats=1,
        silence_seconds=0.0,
        activation_frames=(4,),
        peak_score=0.8,
        processed_frames=10,
        duration_seconds=0.8,
    )

    failures = benchmark_script._acceptance_gate_failures(
        base,
        phase_summary=phase,
        continuous_result=continuous,
        require_zero_false_accepts=True,
        max_false_reject_rate=0.2,
        require_zero_continuous_activations=True,
    )

    assert "base: false_accepts=1" in failures
    assert "continuous: activations=1" in failures


def test_acceptance_gates_fail_when_false_reject_rate_exceeds_limit() -> None:
    base = WakeBenchmarkSummary(
        (
            _summary_result(expected=True, detected=True),
            _summary_result(expected=True, detected=False),
            _summary_result(expected=False, detected=False),
        )
    )

    failures = benchmark_script._acceptance_gate_failures(
        base,
        max_false_reject_rate=0.25,
    )

    assert failures == ["base: false_reject_rate=0.500 > 0.250"]


def test_acceptance_gates_pass_when_limits_are_met() -> None:
    base = WakeBenchmarkSummary(
        (
            _summary_result(expected=True, detected=True),
            _summary_result(expected=False, detected=False),
        )
    )
    phase = WakePhaseBenchmarkSummary(
        (
            _phase_result(expected=True, detected=True),
            _phase_result(expected=False, detected=False),
        )
    )
    continuous = WakeContinuousBenchmarkResult(
        clip_count=1,
        repeats=1,
        silence_seconds=0.0,
        activation_frames=(),
        peak_score=0.3,
        processed_frames=10,
        duration_seconds=0.8,
    )

    assert benchmark_script._acceptance_gate_failures(
        base,
        phase_summary=phase,
        continuous_result=continuous,
        require_zero_false_accepts=True,
        max_false_reject_rate=0.1,
        require_zero_continuous_activations=True,
    ) == []


def test_benchmark_parser_accepts_autonomous_gate_options() -> None:
    args = benchmark_script.build_parser().parse_args(
        [
            "--negative",
            "negative.wav",
            "--continuous-negatives",
            "--require-zero-false-accepts",
            "--max-false-reject-rate",
            "0.10",
            "--require-zero-continuous-activations",
        ]
    )

    assert args.require_zero_false_accepts is True
    assert args.max_false_reject_rate == pytest.approx(0.10)
    assert args.require_zero_continuous_activations is True
