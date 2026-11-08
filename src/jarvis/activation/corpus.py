from __future__ import annotations

from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sys
import wave
from typing import Protocol


class SpeechRecorder(Protocol):
    def record(self) -> list[int]: ...


@dataclass(frozen=True, slots=True)
class WakeCorpusCapture:
    paths: tuple[Path, ...]
    attempts: int
    rejected_attempts: int


def write_pcm16_mono_wav(
    path: Path,
    samples: list[int],
    *,
    sample_rate: int = 16000,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = array("h", samples)
    if sys.byteorder != "little":
        payload.byteswap()
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(payload.tobytes())


def _next_sample_path(directory: Path, label: str) -> Path:
    index = 1
    while True:
        candidate = directory / f"{label}_{index:03d}.wav"
        if not candidate.exists():
            return candidate
        index += 1


def capture_labeled_samples(
    recorder: SpeechRecorder,
    *,
    output_root: Path,
    label: str,
    count: int,
    sample_rate: int = 16000,
    max_attempts: int | None = None,
    before_capture: Callable[[int, int], None] | None = None,
    output: Callable[[str], None] = print,
) -> WakeCorpusCapture:
    if label not in {"positive", "negative"}:
        raise ValueError("label deve ser 'positive' ou 'negative'.")
    if count <= 0:
        raise ValueError("count deve ser positivo.")
    if sample_rate <= 0:
        raise ValueError("sample_rate deve ser positivo.")
    attempt_limit = max_attempts if max_attempts is not None else count * 3
    if attempt_limit < count:
        raise ValueError("max_attempts não pode ser menor que count.")

    directory = output_root / label
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    attempts = 0
    rejected = 0

    while len(paths) < count and attempts < attempt_limit:
        attempts += 1
        if before_capture is not None:
            before_capture(len(paths) + 1, count)
        samples = recorder.record()
        if not samples:
            rejected += 1
            output(
                f"capture rejected attempt={attempts} "
                "reason=no_valid_speech"
            )
            continue

        path = _next_sample_path(directory, label)
        write_pcm16_mono_wav(
            path,
            samples,
            sample_rate=sample_rate,
        )
        paths.append(path)
        output(
            f"captured {len(paths)}/{count} "
            f"duration={len(samples) / sample_rate:.2f}s path={path}"
        )
    return WakeCorpusCapture(
        paths=tuple(paths),
        attempts=attempts,
        rejected_attempts=rejected,
    )
