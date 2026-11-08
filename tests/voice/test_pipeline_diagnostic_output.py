from jarvis.runtime.resource_manager import ResourceManager
from jarvis.voice.pipeline import VoicePipeline

from tests.voice.test_pipeline import (
    FakeHarness,
    FakeListener,
    FakeRecorder,
    FakeSpeechToText,
    FakeTextToSpeech,
    activation,
)


def test_latency_output_failure_does_not_kill_voice_turn() -> None:
    lines: list[str] = []

    def fragile_output(message: str) -> None:
        if message.startswith("[latency]"):
            raise UnicodeEncodeError("cp1252", "≈", 0, 1, "unsupported")
        lines.append(message)

    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1, 2, 3]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness(),
        resource_manager=ResourceManager(),
        text_to_speech=FakeTextToSpeech(),
        output=fragile_output,
        cooldown_waiter=lambda _seconds: None,
        latency_debug_enabled=True,
        debug_enabled=True,
    )

    assert pipeline.process_once() is True
    assert pipeline.state.name == "WAITING_WAKE"
    assert any(line.startswith("[voice]") for line in lines)
