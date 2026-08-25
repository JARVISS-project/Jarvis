from collections.abc import Sequence

from jarvis.activation.listener import WakeWordListener
from jarvis.audio.input import AudioInput
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.state import RuntimeState


class FakeAudioInput(AudioInput):
    def __init__(self, frames: list[list[int]]) -> None:
        self.frames = frames
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def read_frame(self) -> list[int]:
        return self.frames.pop(0)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

class FakeDetector:
    def __init__(self, results: list[bool]) -> None:
        self.results = results
        self.frames: list[Sequence[int]] = []
        self.closed = False
        self.reset_calls = 0

    def process(self, frame: Sequence[int]) -> bool:
        self.frames.append(frame)
        return self.results.pop(0)

    def close(self) -> None:
        self.closed = True

    def reset(self) -> None:
        self.reset_calls += 1


def test_listener_emits_event_when_wake_word_is_detected() -> None:
    audio_input = FakeAudioInput([[1, 2]])
    detector = FakeDetector([True])
    listener = WakeWordListener(audio_input, detector)
    listener.start()

    event = listener.get_event()

    assert event is not None
    assert event.source == "wake_word"
    assert detector.frames == [[1, 2]]


def test_listener_does_not_emit_event_for_normal_frame() -> None:
    listener = WakeWordListener(
        FakeAudioInput([[0, 0]]),
        FakeDetector([False]),
    )
    listener.start()

    assert listener.get_event() is None


def test_listener_releases_audio_and_detector() -> None:
    audio_input = FakeAudioInput([])
    detector = FakeDetector([])
    listener = WakeWordListener(audio_input, detector)
    listener.start()

    listener.close()

    assert audio_input.started is True
    assert audio_input.stopped is True
    assert audio_input.closed is True
    assert detector.closed is True


def test_game_mode_does_not_disable_wake_word() -> None:
    manager = ResourceManager()
    manager.enable_game_mode()
    listener = WakeWordListener(
        FakeAudioInput([[1]]),
        FakeDetector([True]),
    )
    listener.start()

    event = listener.get_event()

    assert manager.state is RuntimeState.GAME_MODE
    assert event is not None


def test_cooldown_suppresses_immediate_duplicate_activation() -> None:
    times = iter([10.0, 10.5, 12.0])
    listener = WakeWordListener(
        FakeAudioInput([[1], [1], [1]]),
        FakeDetector([True, True, True]),
        cooldown_seconds=1.5,
        monotonic_clock=lambda: next(times),
    )
    listener.start()

    assert listener.get_event() is not None
    assert listener.get_event() is None
    assert listener.get_event() is not None


def test_reset_clears_detector_and_restarts_cooldown() -> None:
    times = iter([10.0, 20.0, 20.5])
    detector = FakeDetector([True, True])
    listener = WakeWordListener(
        FakeAudioInput([[1], [1]]),
        detector,
        cooldown_seconds=1.5,
        monotonic_clock=lambda: next(times),
    )
    listener.start()

    assert listener.get_event() is not None
    listener.reset()

    assert listener.get_event() is None
    assert detector.reset_calls == 1
