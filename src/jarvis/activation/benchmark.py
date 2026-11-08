from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path
import sys
import wave
from typing import Protocol, Sequence


class BenchmarkWakeWordDetector(Protocol):
    frame_length: int
    sample_rate: int

    def process(self, frame: Sequence[int]) -> bool: ...

    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class WakeBenchmarkResult:
    path: Path
    expected_activation: bool
    detected: bool
    peak_score: float
    activation_frame: int | None
    processed_frames: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class WakeBenchmarkSummary:
    results: tuple[WakeBenchmarkResult, ...]

    @property
    def positives(self) -> int:
        return sum(result.expected_activation for result in self.results)

    @property
    def negatives(self) -> int:
        return len(self.results) - self.positives

    @property
    def true_positives(self) -> int:
        return sum(
            result.expected_activation and result.detected
            for result in self.results
        )

    @property
    def false_rejects(self) -> int:
        return self.positives - self.true_positives

    @property
    def false_accepts(self) -> int:
        return sum(
            not result.expected_activation and result.detected
            for result in self.results
        )

    @property
    def true_negatives(self) -> int:
        return self.negatives - self.false_accepts

    @property
    def false_reject_rate(self) -> float:
        return (
            self.false_rejects / self.positives
            if self.positives
            else 0.0
        )

    @property
    def false_accept_rate(self) -> float:
        return (
            self.false_accepts / self.negatives
            if self.negatives
            else 0.0
        )


def read_pcm16_mono_wav(path: Path, *, sample_rate: int) -> list[int]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1:
            raise ValueError(f"{path}: WAV deve ser mono.")
        if source.getsampwidth() != 2:
            raise ValueError(f"{path}: WAV deve usar PCM de 16 bits.")

        if source.getframerate() != sample_rate:
            raise ValueError(
                f"{path}: esperado {sample_rate} Hz, "
                f"recebido {source.getframerate()} Hz."
            )
        if source.getcomptype() != "NONE":
            raise ValueError(f"{path}: WAV comprimido não é suportado.")
        frame_count = source.getnframes()
        raw = source.readframes(frame_count)

    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tolist()


def benchmark_wake_clip(
    detector: BenchmarkWakeWordDetector,
    path: Path,
    *,
    expected_activation: bool,
) -> WakeBenchmarkResult:
    samples = read_pcm16_mono_wav(path, sample_rate=detector.sample_rate)
    detector.reset()
    peak_score = 0.0
    activation_frame: int | None = None
    processed_frames = 0

    try:
        for offset in range(0, len(samples), detector.frame_length):
            frame = samples[offset : offset + detector.frame_length]
            if len(frame) < detector.frame_length:
                frame.extend([0] * (detector.frame_length - len(frame)))
            detected = detector.process(frame)
            processed_frames += 1
            peak_score = max(
                peak_score,
                float(getattr(detector, "last_score", 0.0)),
            )
            if detected and activation_frame is None:
                activation_frame = processed_frames - 1

        return WakeBenchmarkResult(
            path=path,
            expected_activation=expected_activation,
            detected=activation_frame is not None,
            peak_score=peak_score,
            activation_frame=activation_frame,
            processed_frames=processed_frames,
            duration_seconds=len(samples) / detector.sample_rate,
        )
    finally:
        detector.reset()


def benchmark_wake_dataset(
    detector: BenchmarkWakeWordDetector,
    *,
    positive_paths: Sequence[Path],
    negative_paths: Sequence[Path],
) -> WakeBenchmarkSummary:
    results = [
        benchmark_wake_clip(
            detector,
            path,
            expected_activation=True,
        )
        for path in positive_paths
    ]
    results.extend(
        benchmark_wake_clip(
            detector,
            path,
            expected_activation=False,
        )
        for path in negative_paths
    )
    return WakeBenchmarkSummary(tuple(results))


@dataclass(frozen=True, slots=True)
class WakePhaseBenchmarkResult:
    path: Path
    expected_activation: bool
    onset_phase_samples: int
    onset_phase_ms: float
    detected: bool
    peak_score: float
    activation_frame: int | None
    processed_frames: int
    clip_duration_seconds: float


@dataclass(frozen=True, slots=True)
class WakePhaseBenchmarkSummary:
    results: tuple[WakePhaseBenchmarkResult, ...]

    @property
    def positives(self) -> int:
        return sum(result.expected_activation for result in self.results)

    @property
    def negatives(self) -> int:
        return len(self.results) - self.positives

    @property
    def true_positives(self) -> int:
        return sum(
            result.expected_activation and result.detected
            for result in self.results
        )

    @property
    def false_rejects(self) -> int:
        return self.positives - self.true_positives

    @property
    def false_accepts(self) -> int:
        return sum(
            not result.expected_activation and result.detected
            for result in self.results
        )

    @property
    def true_negatives(self) -> int:
        return self.negatives - self.false_accepts

    @property
    def false_reject_rate(self) -> float:
        return (
            self.false_rejects / self.positives
            if self.positives
            else 0.0
        )

    @property
    def false_accept_rate(self) -> float:
        return (
            self.false_accepts / self.negatives
            if self.negatives
            else 0.0
        )


def benchmark_wake_clip_phases(
    detector: BenchmarkWakeWordDetector,
    path: Path,
    *,
    expected_activation: bool,
    phase_step_ms: float = 10.0,
    leading_silence_seconds: float = 2.0,
    trailing_silence_seconds: float = 0.24,
) -> tuple[WakePhaseBenchmarkResult, ...]:
    """Evaluate one clip at multiple onset phases within a detector frame.

    Individual WAV benchmarks always begin at frame phase zero. A live
    microphone does not. Each trial is therefore reset independently, primed
    with silence aligned to a detector-frame boundary, and then shifted by a
    sub-frame phase offset before the WAV begins.
    """

    if phase_step_ms <= 0:
        raise ValueError("phase_step_ms deve ser positivo.")
    if leading_silence_seconds < 0:
        raise ValueError(
            "leading_silence_seconds não pode ser negativo."
        )
    if trailing_silence_seconds < 0:
        raise ValueError(
            "trailing_silence_seconds não pode ser negativo."
        )

    phase_step_samples = int(
        round(detector.sample_rate * phase_step_ms / 1000.0)
    )
    if phase_step_samples <= 0:
        raise ValueError(
            "phase_step_ms é pequeno demais para a taxa de amostragem."
        )
    if phase_step_samples > detector.frame_length:
        raise ValueError(
            "phase_step_ms não pode exceder a duração de um frame."
        )

    samples = read_pcm16_mono_wav(
        path,
        sample_rate=detector.sample_rate,
    )
    requested_silence_samples = int(
        round(detector.sample_rate * leading_silence_seconds)
    )
    aligned_silence_samples = 0
    if requested_silence_samples:
        aligned_silence_samples = (
            (
                requested_silence_samples
                + detector.frame_length
                - 1
            )
            // detector.frame_length
        ) * detector.frame_length

    results: list[WakePhaseBenchmarkResult] = []
    for phase_samples in range(
        0,
        detector.frame_length,
        phase_step_samples,
    ):
        trailing_silence_samples = int(
            round(
                detector.sample_rate
                * trailing_silence_seconds
            )
        )
        stream = (
            [0] * (aligned_silence_samples + phase_samples)
            + samples
            + [0] * trailing_silence_samples
        )
        detector.reset()
        peak_score = 0.0
        activation_frame: int | None = None
        processed_frames = 0

        try:
            for offset in range(
                0,
                len(stream),
                detector.frame_length,
            ):
                frame = stream[
                    offset : offset + detector.frame_length
                ]
                if len(frame) < detector.frame_length:
                    frame.extend(
                        [0] * (detector.frame_length - len(frame))
                    )
                detected = detector.process(frame)
                processed_frames += 1
                peak_score = max(
                    peak_score,
                    float(getattr(detector, "last_score", 0.0)),
                )
                if detected and activation_frame is None:
                    activation_frame = processed_frames - 1
        finally:
            detector.reset()

        results.append(
            WakePhaseBenchmarkResult(
                path=path,
                expected_activation=expected_activation,
                onset_phase_samples=phase_samples,
                onset_phase_ms=(
                    phase_samples
                    * 1000.0
                    / detector.sample_rate
                ),
                detected=activation_frame is not None,
                peak_score=peak_score,
                activation_frame=activation_frame,
                processed_frames=processed_frames,
                clip_duration_seconds=(
                    len(samples) / detector.sample_rate
                ),
            )
        )

    return tuple(results)


def benchmark_wake_dataset_phases(
    detector: BenchmarkWakeWordDetector,
    *,
    positive_paths: Sequence[Path],
    negative_paths: Sequence[Path],
    phase_step_ms: float = 10.0,
    leading_silence_seconds: float = 2.0,
    trailing_silence_seconds: float = 0.24,
) -> WakePhaseBenchmarkSummary:
    results: list[WakePhaseBenchmarkResult] = []
    for path in positive_paths:
        results.extend(
            benchmark_wake_clip_phases(
                detector,
                path,
                expected_activation=True,
                phase_step_ms=phase_step_ms,
                leading_silence_seconds=leading_silence_seconds,
                trailing_silence_seconds=trailing_silence_seconds,
            )
        )
    for path in negative_paths:
        results.extend(
            benchmark_wake_clip_phases(
                detector,
                path,
                expected_activation=False,
                phase_step_ms=phase_step_ms,
                leading_silence_seconds=leading_silence_seconds,
                trailing_silence_seconds=trailing_silence_seconds,
            )
        )
    return WakePhaseBenchmarkSummary(tuple(results))


@dataclass(frozen=True, slots=True)
class WakeContinuousBenchmarkResult:
    clip_count: int
    repeats: int
    silence_seconds: float
    activation_frames: tuple[int, ...]
    peak_score: float
    processed_frames: int
    duration_seconds: float

    @property
    def activations(self) -> int:
        return len(self.activation_frames)

    @property
    def detected(self) -> bool:
        return bool(self.activation_frames)


def benchmark_continuous_negative_stream(
    detector: BenchmarkWakeWordDetector,
    *,
    negative_paths: Sequence[Path],
    repeats: int = 1,
    silence_seconds: float = 0.0,
) -> WakeContinuousBenchmarkResult:
    """Replay negatives as one uninterrupted PCM stream.

    Unlike benchmark_wake_clip, this deliberately does not reset or pad at
    clip boundaries. Trailing samples from one WAV become the beginning of
    the next detector frame, matching a continuously running microphone.
    """

    paths = tuple(negative_paths)
    if not paths:
        raise ValueError("negative_paths não pode ser vazio.")
    if repeats <= 0:
        raise ValueError("repeats deve ser positivo.")
    if silence_seconds < 0:
        raise ValueError("silence_seconds não pode ser negativo.")

    silence_samples = int(round(detector.sample_rate * silence_seconds))
    frame_length = detector.frame_length
    pending: list[int] = []
    activation_frames: list[int] = []
    peak_score = 0.0
    processed_frames = 0
    total_samples = 0
    total_clips = len(paths) * repeats
    clip_index = 0

    def process_pending(*, final: bool = False) -> None:
        nonlocal pending, peak_score, processed_frames
        while len(pending) >= frame_length or (final and pending):
            frame = pending[:frame_length]
            del pending[:frame_length]
            if len(frame) < frame_length:
                frame.extend([0] * (frame_length - len(frame)))
            detected = detector.process(frame)
            peak_score = max(
                peak_score,
                float(getattr(detector, "last_score", 0.0)),
            )
            if detected:
                activation_frames.append(processed_frames)
            processed_frames += 1

    detector.reset()
    try:
        for _repeat in range(repeats):
            for path in paths:
                samples = read_pcm16_mono_wav(
                    path,
                    sample_rate=detector.sample_rate,
                )
                pending.extend(samples)
                total_samples += len(samples)
                clip_index += 1

                if silence_samples and clip_index < total_clips:
                    pending.extend([0] * silence_samples)
                    total_samples += silence_samples

                process_pending()

        process_pending(final=True)
        return WakeContinuousBenchmarkResult(
            clip_count=len(paths),
            repeats=repeats,
            silence_seconds=silence_seconds,
            activation_frames=tuple(activation_frames),
            peak_score=peak_score,
            processed_frames=processed_frames,
            duration_seconds=total_samples / detector.sample_rate,
        )
    finally:
        detector.reset()
