from jarvis.voice.audio_session import VoiceAudioSession


class FakeListener:
    def __init__(self) -> None:
        self.audio_input = object()
        self.started = False
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        if not self.started:
            self.started = True
            self.starts += 1

    def stop(self) -> None:
        if self.started:
            self.started = False
            self.stops += 1


class FakeRecorder:
    def __init__(self, listener: FakeListener) -> None:
        self.listener = listener

    def record(self) -> list[int]:
        assert self.listener.started
        return [1, 2]


class FakeTTS:
    def __init__(self, listener: FakeListener) -> None:
        self.listener = listener

    def speak(self, text: str) -> None:
        assert not self.listener.started


def test_one_session_serializes_tts_and_capture_without_second_input() -> None:
    listener = FakeListener()
    session = VoiceAudioSession(listener)  # type: ignore[arg-type]
    listener.start()
    audio = session.speak_then_capture(
        "Confirma?",
        FakeTTS(listener),  # type: ignore[arg-type]
        FakeRecorder(listener),  # type: ignore[arg-type]
    )
    assert audio == [1, 2]
    assert session.audio_input is listener.audio_input
    assert listener.starts == 2
    assert listener.stops == 1
    assert session.owner is None
