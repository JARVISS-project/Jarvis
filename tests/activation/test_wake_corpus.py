import json
from pathlib import Path

import pytest

from scripts import capture_wake_samples as capture_script
from jarvis.activation.benchmark import read_pcm16_mono_wav
from jarvis.activation.corpus import (
    capture_labeled_samples,
    write_pcm16_mono_wav,
)


class FakeRecorder:
    def __init__(self, clips: list[list[int]]) -> None:
        self.clips = list(clips)

    def record(self) -> list[int]:
        return self.clips.pop(0)


def test_capture_retries_empty_audio_and_never_overwrites(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "voice-benchmark"
    existing = output_root / "positive" / "positive_001.wav"
    write_pcm16_mono_wav(existing, [10, 20])

    recorder = FakeRecorder([[], [100, 200], [300, 400]])
    cues: list[tuple[int, int]] = []
    messages: list[str] = []
    result = capture_labeled_samples(
        recorder,
        output_root=output_root,
        label="positive",
        count=2,
        max_attempts=3,
        before_capture=lambda index, total: cues.append((index, total)),
        output=messages.append,
    )

    assert result.attempts == 3
    assert result.rejected_attempts == 1
    assert [path.name for path in result.paths] == [
        "positive_002.wav",
        "positive_003.wav",
    ]
    assert cues == [(1, 2), (1, 2), (2, 2)]
    assert read_pcm16_mono_wav(
        result.paths[0],
        sample_rate=16000,
    ) == [100, 200]
    assert existing.exists()
    assert any("reason=no_valid_speech" in message for message in messages)


@pytest.mark.parametrize(
    ("label", "count", "max_attempts"),
    [
        ("other", 1, None),
        ("positive", 0, None),
        ("negative", 2, 1),
    ],
)
def test_capture_validates_arguments(
    tmp_path: Path,
    label: str,
    count: int,
    max_attempts: int | None,
) -> None:
    with pytest.raises(ValueError):
        capture_labeled_samples(
            FakeRecorder([[1]]),
            output_root=tmp_path,
            label=label,
            count=count,
            max_attempts=max_attempts,
            output=lambda _message: None,
        )


class FakeAudioInput:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def stop(self) -> None:
        self.events.append("stop")

    def start(self) -> None:
        self.events.append("start")


def test_arm_capture_starts_microphone_only_after_gate(monkeypatch) -> None:
    events: list[str] = []
    audio = FakeAudioInput(events)
    monkeypatch.setattr(capture_script, "_cue", lambda *args, **kwargs: events.append("cue"))
    monkeypatch.setattr("builtins.input", lambda _prompt: events.append("gate"))
    monkeypatch.setattr(capture_script.time, "sleep", lambda delay: events.append(f"sleep:{delay}"))
    monkeypatch.setattr(
        capture_script,
        "_signal_ready",
        lambda *, beep: events.append(f"signal:{beep}"),
    )

    capture_script._arm_capture(
        audio,
        index=1,
        total=1,
        label="negative",
        beep=True,
        prompt_text="banana azul",
        arm_delay=0.5,
        wait_for_enter=True,
    )
    assert events == [
        "stop",
        "cue",
        "gate",
        "sleep:0.5",
        "start",
        "signal:True",
    ]


def test_manifest_records_expected_phrase(tmp_path: Path) -> None:
    clip = tmp_path / "negative" / "negative_001.wav"
    capture_script._append_manifest(
        tmp_path,
        label="negative",
        prompt_text="banana azul",
        paths=(clip,),
    )

    lines = (tmp_path / "capture_manifest.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {
        "path": str(clip),
        "label": "negative",
        "expected_phrase": "banana azul",
    }


def test_manifest_records_capture_scenario(tmp_path: Path) -> None:
    clip = tmp_path / "positive" / "positive_001.wav"
    capture_script._append_manifest(
        tmp_path,
        label="positive",
        prompt_text="Hey Jarvis",
        scenario="far-tv-noise",
        paths=(clip,),
    )

    payload = json.loads(
        (tmp_path / "capture_manifest.jsonl").read_text(
            encoding="utf-8"
        ).strip()
    )
    assert payload["expected_phrase"] == "Hey Jarvis"
    assert payload["scenario"] == "far-tv-noise"


class FakeRawAudioInput:
    def __init__(self, frames: list[list[int]]) -> None:
        self.frames = list(frames)

    def read_frame(self) -> list[int]:
        return self.frames.pop(0)


def test_fixed_window_recorder_captures_raw_pcm_without_speech_gate() -> None:
    audio = FakeRawAudioInput(
        [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]
    )
    recorder = capture_script.FixedWindowRecorder(
        audio,
        sample_rate=4,
        duration_seconds=2.5,
    )

    assert recorder.record() == list(range(1, 11))


def test_fixed_window_recorder_can_reject_window_without_speech() -> None:
    audio = FakeRawAudioInput([[0] * 10 for _ in range(10)])
    recorder = capture_script.FixedWindowRecorder(
        audio,
        sample_rate=100,
        duration_seconds=1.0,
        require_speech_presence=True,
        speech_threshold=500.0,
        minimum_speech_duration_seconds=0.24,
        minimum_audio_energy=700.0,
    )

    assert recorder.record() == []


def test_fixed_window_recorder_keeps_full_raw_window_when_speech_is_valid() -> None:
    frames = [[0] * 10 for _ in range(3)]
    frames += [[1200] * 10 for _ in range(4)]
    frames += [[0] * 10 for _ in range(3)]
    audio = FakeRawAudioInput(frames)
    recorder = capture_script.FixedWindowRecorder(
        audio,
        sample_rate=100,
        duration_seconds=1.0,
        require_speech_presence=True,
        speech_threshold=500.0,
        minimum_speech_duration_seconds=0.24,
        minimum_audio_energy=700.0,
    )

    captured = recorder.record()

    assert len(captured) == 100
    assert captured[:30] == [0] * 30
    assert 1200 in captured


def test_fixed_window_recorder_rejects_non_positive_duration() -> None:
    with pytest.raises(ValueError, match="duration_seconds"):
        capture_script.FixedWindowRecorder(
            FakeRawAudioInput([[1]]),
            sample_rate=16000,
            duration_seconds=0,
        )
