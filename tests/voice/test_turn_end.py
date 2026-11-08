import pytest

from jarvis.runtime.resource_manager import ResourceManager
from jarvis.speech.base import SpeechToText, TranscriptionResult
from jarvis.voice.pipeline import VoicePipeline
from jarvis.voice.turn_end import IncompleteUtteranceDetector
from tests.voice.test_pipeline import (
    FakeHarness,
    FakeListener,
    SequenceRecorder,
    activation,
)


class CombinedSTT(SpeechToText):
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.audio: list[list[int]] = []

    def transcribe(
        self, audio: list[int], sample_rate: int
    ) -> TranscriptionResult:
        self.audio.append(audio)
        return TranscriptionResult(self.texts.pop(0), "pt", 0.5)


@pytest.mark.parametrize(
    "text",
    [
        "Agora eu queria saber...",
        "Eu gostaria de...",
        "Eu queria que você...",
        "Nesse caso...",
        "Porque...",
        "Então...",
        "Mas...",
        "E...",
        "E sobre...",
        "E aquela...",
        "O que acontece se...",
        "Agora eu volto pras minhas...",
        "Nesse caso é...",
        "Olha, eu queria...",
        "Agora deixa eu...",
    ],
)
def test_incomplete_endings_are_detected(text: str) -> None:
    assert IncompleteUtteranceDetector().is_possibly_incomplete(text)


@pytest.mark.parametrize(
    "text",
    [
        "Que horas são?",
        "Abra a segunda.",
        "Procure minhas notas.",
        "Leia ela.",
    ],
)
def test_complete_endings_do_not_request_extension(text: str) -> None:
    assert not IncompleteUtteranceDetector().is_possibly_incomplete(text)


def test_continuation_is_combined_into_one_final_message() -> None:
    harness = FakeHarness()
    stt = CombinedSTT(
        ["Agora eu queria saber...", "Agora eu queria saber das minhas notas"]
    )
    extension = SequenceRecorder([[2]])
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        incomplete_turn_recorder=extension,  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
    )

    pipeline.process_once()

    assert stt.audio == [[1], [1, 2]]
    assert harness.calls == [
        ("Agora eu queria saber das minhas notas", False)
    ]
    assert extension.calls == 1


def test_real_incomplete_context_switch_is_combined_once() -> None:
    harness = FakeHarness()
    stt = CombinedSTT(
        [
            "Agora eu volto pras minhas...",
            "Agora eu volto pras minhas notas do Obsidian.",
        ]
    )
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        incomplete_turn_recorder=SequenceRecorder([[2]]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
    )

    pipeline.process_once()

    assert harness.calls == [
        ("Agora eu volto pras minhas notas do Obsidian.", False)
    ]
    assert stt.audio == [[1], [1, 2]]


def test_extension_timeout_processes_original_once() -> None:
    harness = FakeHarness()
    stt = CombinedSTT(["Eu gostaria de..."])
    extension = SequenceRecorder([[]])
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        incomplete_turn_recorder=extension,  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
    )

    pipeline.process_once()

    assert stt.audio == [[1]]
    assert harness.calls == [("Eu gostaria de...", False)]
    assert extension.calls == 1


def test_complete_phrase_has_no_extra_capture() -> None:
    harness = FakeHarness()
    stt = CombinedSTT(["Que horas são?"])
    extension = SequenceRecorder([[2]])
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        incomplete_turn_recorder=extension,  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
    )

    pipeline.process_once()

    assert extension.calls == 0
    assert harness.calls == [("Que horas são?", False)]
