from typing import Any

from jarvis.audio.input import PvRecorderAudioInput


class FakeRecorder:
    def __init__(self, **kwargs: Any) -> None:
        self.arguments = kwargs
        self.started = False
        self.stopped = False
        self.deleted = False

    def start(self) -> None:
        self.started = True

    def read(self) -> list[int]:
        return [1, 2, 3]

    def stop(self) -> None:
        self.stopped = True

    def delete(self) -> None:
        self.deleted = True


def test_microphone_adapter_reads_small_pcm_frame_and_releases_device() -> None:
    recorder = FakeRecorder()
    audio_input = PvRecorderAudioInput(
        frame_length=3,
        device_index=2,
        recorder_factory=lambda **_kwargs: recorder,
    )

    audio_input.start()
    frame = audio_input.read_frame()
    audio_input.close()

    assert frame == [1, 2, 3]
    assert recorder.started is True
    assert recorder.stopped is False
    assert recorder.deleted is True
    assert audio_input.sample_rate == 16000
    assert audio_input.sample_width_bits == 16


def test_close_uses_single_owning_delete_even_if_stop_would_fail() -> None:
    class StopFails(FakeRecorder):
        def stop(self) -> None:
            raise OSError("Windows device already closing")

    recorder = StopFails()
    audio_input = PvRecorderAudioInput(
        frame_length=3, recorder_factory=lambda **_kwargs: recorder,
    )
    audio_input.start()
    audio_input.close()
    audio_input.close()
    assert recorder.deleted is True


def test_default_microphone_device_uses_system_default() -> None:
    captured_arguments: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeRecorder:
        captured_arguments.update(kwargs)
        return FakeRecorder()

    audio_input = PvRecorderAudioInput(
        frame_length=512,
        recorder_factory=factory,
    )

    audio_input.close()

    assert captured_arguments == {
        "device_index": -1,
        "frame_length": 512,
    }
