from pathlib import Path

import pytest

from jarvis.security.audit import AuditLogger
from jarvis.security.confirmation import ConfirmationProvider, VoiceConfirmation
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.state import RuntimeState
from jarvis.speech.base import TranscriptionResult
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.executor import ToolExecutor
from jarvis.security.filesystem import FileSystemGuard


class FakeListener:
    def __init__(self, operations: list[str]) -> None:
        self.operations = operations
        self.started = True

    def stop(self) -> None:
        self.started = False
        self.operations.append("listener.stop")

    def start(self) -> None:
        self.started = True
        self.operations.append("listener.start")

    def reset(self) -> None:
        self.operations.append("listener.reset")


class FakeRecorder:
    def __init__(self, audio: list[int], operations: list[str]) -> None:
        self.audio = audio
        self.operations = operations

    def record(self) -> list[int]:
        self.operations.append("capture")
        return self.audio


class FakeSTT:
    def __init__(
        self,
        text: str = "sim",
        error: Exception | None = None,
        no_speech: float | None = None,
    ) -> None:
        self.text = text
        self.error = error
        self.no_speech = no_speech
        self.calls = 0

    def transcribe(self, audio, sample_rate):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.error:
            raise self.error
        return TranscriptionResult(
            self.text, "pt", 0.5, no_speech_probability=self.no_speech
        )


class FakeTTS:
    def __init__(self, operations: list[str]) -> None:
        self.operations = operations
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        self.operations.append(f"tts:{text}")


class FixedConfirmation(ConfirmationProvider):
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.calls = 0

    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        self.calls += 1
        return self.answer


def provider(
    text: str = "sim",
    *,
    audio: list[int] | None = None,
    error: Exception | None = None,
    fallback: ConfirmationProvider | None = None,
    keyboard_fallback: bool = False,
    no_speech: float | None = None,
) -> tuple[VoiceConfirmation, list[str], FakeSTT, ResourceManager, FakeTTS]:
    operations: list[str] = []
    manager = ResourceManager()
    manager.activate()
    stt = FakeSTT(text, error, no_speech)
    tts = FakeTTS(operations)
    confirmation = VoiceConfirmation(
        listener=FakeListener(operations),  # type: ignore[arg-type]
        recorder=FakeRecorder(audio if audio is not None else [1] * 3000, operations),  # type: ignore[arg-type]
        speech_to_text=stt,  # type: ignore[arg-type]
        text_to_speech=tts,  # type: ignore[arg-type]
        resource_manager=manager,
        keyboard_fallback=keyboard_fallback,
        fallback=fallback,
        output=lambda message: operations.append(f"output:{message}"),
    )
    return confirmation, operations, stt, manager, tts


@pytest.mark.parametrize("phrase", ["sim", "confirmo", "CONFIRMAR!", "autorizo", "pode", "pode fazer"])
def test_explicit_affirmatives_authorize(phrase: str) -> None:
    confirmation, _, _, _, _ = provider(phrase)
    assert confirmation.confirm("delete_file", "README.md", RiskLevel.HIGH)


@pytest.mark.parametrize("phrase", ["não", "cancelar", "cancela", "não autorizo", "parar"])
def test_explicit_negatives_deny(phrase: str) -> None:
    confirmation, _, _, _, _ = provider(phrase)
    assert not confirmation.confirm("delete_file", "README.md", RiskLevel.HIGH)


@pytest.mark.parametrize("phrase", ["", "talvez", "acho que sim", "sim talvez"])
def test_empty_unknown_and_ambiguous_phrases_fail_closed(phrase: str) -> None:
    confirmation, _, _, _, _ = provider(phrase)
    assert not confirmation.confirm("delete_file", "README.md", RiskLevel.HIGH)


def test_silence_stt_error_and_no_speech_fail_closed() -> None:
    silence, _, stt, _, _ = provider(audio=[])
    assert not silence.confirm("delete_file", "README.md", RiskLevel.HIGH)
    assert stt.calls == 0
    failed, _, _, _, _ = provider(error=RuntimeError("STT falhou"))
    assert not failed.confirm("delete_file", "README.md", RiskLevel.HIGH)
    no_speech, _, _, _, _ = provider(no_speech=0.9)
    assert not no_speech.confirm("delete_file", "README.md", RiskLevel.HIGH)


def test_tts_finishes_before_capture_and_listener_is_suspended() -> None:
    confirmation, operations, _, manager, _ = provider()
    confirmation.confirm("delete_file", "README.md", RiskLevel.HIGH)
    prompt_index = next(
        i for i, item in enumerate(operations)
        if item.startswith("tts:Entendi")
    )
    assert prompt_index < operations.index("listener.start") < operations.index("capture")
    assert operations[0] == "listener.stop"
    assert manager.state is RuntimeState.ACTIVE


def test_second_unknown_cancels_without_keyboard_fallback() -> None:
    fallback = FixedConfirmation(True)
    confirmation, _, _, _, _ = provider(
        "talvez", fallback=fallback, keyboard_fallback=True
    )
    assert not confirmation.confirm("delete_file", "README.md", RiskLevel.HIGH)
    assert fallback.calls == 0


def test_unknown_gets_only_one_retry() -> None:
    confirmation, _, stt, _, tts = provider("talvez")
    assert not confirmation.confirm("delete_file", "README.md", RiskLevel.HIGH)
    assert stt.calls == 2
    assert sum("Diga sim ou não" in text for text in tts.spoken) == 1


def test_keyboard_fallback_still_handles_capture_failure() -> None:
    fallback = FixedConfirmation(True)
    confirmation, _, _, _, _ = provider(
        audio=[], fallback=fallback, keyboard_fallback=True
    )
    assert confirmation.confirm("delete_file", "README.md", RiskLevel.HIGH)
    assert fallback.calls == 1


def test_tool_executes_only_after_provider_authorizes(tmp_path: Path) -> None:
    denied = FixedConfirmation(False)
    executor = ToolExecutor(
        SafetyManager(denied), AuditLogger(tmp_path / "audit.log")
    )
    tool = CreateFileTool(FileSystemGuard(tmp_path))
    executor.execute(tool, path="negado.txt", content="x")
    assert not (tmp_path / "negado.txt").exists()
    allowed = FixedConfirmation(True)
    executor.safety_manager = SafetyManager(allowed)
    executor.execute(tool, path="permitido.txt", content="x")
    assert (tmp_path / "permitido.txt").exists()
