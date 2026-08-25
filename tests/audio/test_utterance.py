from jarvis.audio.input import AudioInput
from jarvis.audio.utterance import UtteranceRecorder


class FakeAudioInput(AudioInput):
    def __init__(self, frames: list[list[int]]) -> None:
        self.frames = frames
        self.read_count = 0

    def start(self) -> None:
        pass

    def read_frame(self) -> list[int]:
        self.read_count += 1
        return self.frames.pop(0)

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_recording_ends_after_configured_silence() -> None:
    audio_input = FakeAudioInput(
        [[1000, 1000], [1000, 1000], [0, 0], [0, 0], [1000, 1000]]
    )
    recorder = UtteranceRecorder(
        audio_input,
        sample_rate=10,
        silence_threshold=100,
        silence_duration_seconds=0.4,
        max_recording_seconds=2,
        pre_speech_timeout_seconds=1,
    )

    audio = recorder.record()

    assert audio == [1000, 1000, 1000, 1000, 0, 0, 0, 0]
    assert audio_input.read_count == 4


def test_timeout_before_speech_returns_empty_audio() -> None:
    audio_input = FakeAudioInput([[0, 0], [0, 0], [1000, 1000]])
    recorder = UtteranceRecorder(
        audio_input,
        sample_rate=10,
        silence_threshold=100,
        silence_duration_seconds=0.4,
        max_recording_seconds=2,
        pre_speech_timeout_seconds=0.4,
    )

    assert recorder.record() == []
    assert audio_input.read_count == 2


def test_max_recording_duration_bounds_audio_buffer() -> None:
    audio_input = FakeAudioInput(
        [[1000, 1000], [1000, 1000], [1000, 1000], [1000, 1000]]
    )
    recorder = UtteranceRecorder(
        audio_input,
        sample_rate=10,
        silence_threshold=100,
        silence_duration_seconds=1,
        max_recording_seconds=0.5,
        pre_speech_timeout_seconds=0.2,
    )

    audio = recorder.record()

    assert len(audio) == 5
    assert audio_input.read_count == 3


def test_audio_shorter_than_minimum_speech_is_rejected() -> None:
    recorder = UtteranceRecorder(
        FakeAudioInput([[1000, 1000], [0, 0], [0, 0]]),
        sample_rate=10,
        silence_threshold=100,
        silence_duration_seconds=0.4,
        max_recording_seconds=1,
        pre_speech_timeout_seconds=0.2,
        minimum_speech_duration_seconds=0.3,
        minimum_audio_energy=500,
    )

    assert recorder.record() == []


def test_audio_below_minimum_energy_is_rejected() -> None:
    recorder = UtteranceRecorder(
        FakeAudioInput([[200, 200], [200, 200], [0, 0], [0, 0]]),
        sample_rate=10,
        silence_threshold=100,
        silence_duration_seconds=0.4,
        max_recording_seconds=1,
        pre_speech_timeout_seconds=0.2,
        minimum_speech_duration_seconds=0.3,
        minimum_audio_energy=500,
    )

    assert recorder.record() == []


def test_adaptive_threshold_tracks_noise_floor_and_detects_speech() -> None:
    recorder = UtteranceRecorder(
        FakeAudioInput([[200, 200], [600, 600], [0, 0], [0, 0]]),
        sample_rate=10,
        silence_threshold=100,
        silence_duration_seconds=0.4,
        max_recording_seconds=1,
        pre_speech_timeout_seconds=0.4,
        minimum_speech_duration_seconds=0.1,
        minimum_audio_energy=500,
        noise_calibration_seconds=0.2,
        noise_margin=200,
        pre_roll_seconds=0,
    )
    assert recorder.record() == [600, 600, 0, 0, 0, 0]
    assert recorder.last_noise_floor == 200
    assert recorder.last_speech_threshold == 400


def test_pre_roll_is_preserved_but_short_noise_is_not_speech() -> None:
    recorder = UtteranceRecorder(
        FakeAudioInput([[50, 50], [1000, 1000], [0, 0], [0, 0]]),
        sample_rate=10,
        silence_threshold=500,
        silence_duration_seconds=0.4,
        max_recording_seconds=1,
        pre_speech_timeout_seconds=0.5,
        minimum_speech_duration_seconds=0.1,
        minimum_audio_energy=700,
        pre_roll_seconds=0.2,
    )
    assert recorder.record() == [50, 50, 1000, 1000, 0, 0, 0, 0]


def test_short_pause_does_not_end_utterance_before_end_silence() -> None:
    frames = [
        [1000, 1000],
        [0, 0], [0, 0],
        [1000, 1000],
        [0, 0], [0, 0], [0, 0], [0, 0], [0, 0],
    ]
    recorder = UtteranceRecorder(
        FakeAudioInput(frames),
        sample_rate=10,
        silence_threshold=100,
        silence_duration_seconds=0.4,
        end_silence_seconds=1.0,
        max_recording_seconds=2,
        pre_speech_timeout_seconds=0.5,
        minimum_speech_duration_seconds=0.1,
        minimum_audio_energy=500,
        pre_roll_seconds=0,
    )
    audio = recorder.record()
    assert audio[:8] == [1000, 1000, 0, 0, 0, 0, 1000, 1000]
    assert len(audio) == 18
