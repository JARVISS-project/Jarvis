from threading import Event

from jarvis.audio.input import AudioInput
from jarvis.tts.base import SpeechPlaybackHandle
from jarvis.voice.barge_in import BargeInController


class FakeAudioInput(AudioInput):
    def __init__(self, frames: list[list[int]], on_read=None) -> None:  # type: ignore[no-untyped-def]
        self.frames = frames
        self.on_read = on_read
        self.starts = 0

    def start(self) -> None:
        self.starts += 1

    def read_frame(self) -> list[int]:
        frame = self.frames.pop(0)
        if self.on_read:
            self.on_read(len(self.frames))
        return frame

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


def handle() -> tuple[SpeechPlaybackHandle, Event, list[bool]]:
    completed = Event()
    stopped: list[bool] = []
    playback = SpeechPlaybackHandle(
        completed,
        lambda: (stopped.append(True), completed.set()),
    )
    return playback, completed, stopped


def test_normal_playback_finishes_without_interruption() -> None:
    playback, completed, stopped = handle()
    audio = FakeAudioInput([[100, 100]], lambda _left: completed.set())
    controller = BargeInController(
        audio, sample_rate=10, grace_period_seconds=0,
        minimum_speech_duration_seconds=0.2, energy_margin=500,
    )
    assert controller.listen(playback) is None
    assert stopped == []


def test_sustained_high_energy_interrupts_and_returns_one_utterance() -> None:
    playback, _, stopped = handle()
    audio = FakeAudioInput(
        [[100, 100], [2000, 2000], [2000, 2000], [0, 0], [0, 0], [0, 0]]
    )
    controller = BargeInController(
        audio, sample_rate=10, grace_period_seconds=0.2,
        minimum_speech_duration_seconds=0.4, energy_margin=500,
        silence_duration_seconds=0.4, pre_roll_seconds=0.2,
    )
    captured = controller.listen(playback)
    assert stopped == [True]
    assert captured is not None
    assert [2000, 2000] in [captured[i:i + 2] for i in range(0, len(captured), 2)]


def test_short_noise_and_low_energy_do_not_interrupt() -> None:
    playback, completed, stopped = handle()
    frames = [[100, 100], [2000, 2000], [100, 100]]
    audio = FakeAudioInput(frames, lambda left: completed.set() if left == 0 else None)
    controller = BargeInController(
        audio, sample_rate=10, grace_period_seconds=0.2,
        minimum_speech_duration_seconds=0.4, energy_margin=500,
    )
    assert controller.listen(playback) is None
    assert stopped == []
