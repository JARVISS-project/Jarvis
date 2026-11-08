import io
import json
import wave

from scripts.launch_stt_23w import Utf8Tee

from jarvis.voice.diagnostic import VoiceDiagnosticRecorder
from jarvis.voice.pipeline import VoicePipeline
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.speech.base import TranscriptionResult
from tests.voice.test_pipeline import (
    FakeHarness,
    FakeListener,
    FakeRecorder,
    FakeSpeechToText,
    activation,
)


def test_utf8_console_and_file_round_trip(tmp_path) -> None:
    phrase = "são, você, não, música\n"
    console = io.StringIO()
    path = tmp_path / "console.log"
    tee = Utf8Tee(console, path)

    tee.write(phrase)
    tee.close()

    assert console.getvalue() == phrase
    assert path.read_bytes() == phrase.encode("utf-8")
    assert path.read_text(encoding="utf-8") == phrase


def test_structured_trace_keeps_unicode_and_bounded_wav(tmp_path) -> None:
    trace_path = tmp_path / "turns.jsonl"
    diagnostic = VoiceDiagnosticRecorder(
        trace_path, wav_directory=tmp_path / "wavs", wav_limit=1
    )
    diagnostic.new_turn()
    diagnostic.record("stt_initial", text="Que horas são?")
    diagnostic.new_capture()
    diagnostic.save_wav([0, 1, -1], 16000)
    diagnostic.save_wav([2], 16000)

    records = [json.loads(line) for line in trace_path.read_text(
        encoding="utf-8"
    ).splitlines()]
    assert records[0]["turn_id"] == "turn-01"
    assert records[0]["text"] == "Que horas são?"
    assert records[1]["sha256"]
    assert len(list((tmp_path / "wavs").glob("*.wav"))) == 1
    with wave.open(str(tmp_path / "wavs/attempt_01.wav"), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 3


def test_voice_turn_trace_covers_capture_stt_and_response(tmp_path) -> None:
    diagnostic = VoiceDiagnosticRecorder(
        tmp_path / "turns.jsonl", wav_directory=tmp_path / "wavs", wav_limit=1
    )
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([500] * 16000),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(
            TranscriptionResult("Que horas são?", "pt", 1.0)
        ),
        harness=FakeHarness("Agora são 10 horas."),
        resource_manager=ResourceManager(),
        output=lambda _message: None,
        diagnostic=diagnostic,
    )

    assert pipeline.process_once() is True

    entries = [json.loads(line) for line in (tmp_path / "turns.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()]
    names = {entry["event"] for entry in entries}
    assert {"wake_accepted", "capture_ended", "capture_wav", "stt_initial",
            "stt_selected", "response_formatted", "capture_classification"} <= names
    assert {entry["turn_id"] for entry in entries} == {"turn-01"}
    assert len(list((tmp_path / "wavs").glob("*.wav"))) == 1
