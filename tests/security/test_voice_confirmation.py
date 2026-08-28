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
from jarvis.security.confirmation_request import ConfirmationRequest
from jarvis.voice.state import VoicePipelineState


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


class FakePlayback:
    def __init__(self, operations: list[str]) -> None:
        self.operations = operations

    def wait(self) -> bool:
        self.operations.append("playback.wait")
        return True


class FakeAsyncTTS(FakeTTS):
    def speak_async(self, text: str) -> FakePlayback:
        self.spoken.append(text)
        self.operations.append(f"tts.async:{text}")
        return FakePlayback(self.operations)


class SequenceRecorder:
    def __init__(
        self,
        audio: list[list[int]],
        operations: list[str],
        pending: list[ConfirmationRequest | None] | None = None,
        confirmation: VoiceConfirmation | None = None,
    ) -> None:
        self.audio = audio
        self.operations = operations
        self.pending = pending
        self.confirmation = confirmation
        self.calls = 0

    def record(self) -> list[int]:
        self.calls += 1
        self.operations.append(f"capture:{self.calls}")
        if self.pending is not None and self.confirmation is not None:
            self.pending.append(self.confirmation.pending_request)
        return self.audio.pop(0)


class SequenceSTT:
    def __init__(self, results: list[TranscriptionResult]) -> None:
        self.results = results
        self.calls = 0

    def transcribe(self, audio, sample_rate):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.results.pop(0)


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


@pytest.mark.parametrize(
    "phrase",
    [
        "sim", "confirmo", "CONFIRMAR!", "autorizo", "pode",
        "pode fazer", "isso", "manda ver",
    ],
)
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


def test_confirmation_prompt_is_visible_in_terminal_output() -> None:
    confirmation, operations, _, _, _ = provider("sim")

    assert confirmation.confirm(
        "obsidian_append_note", "Teste para Jarvis.md", RiskLevel.MEDIUM
    )

    assert any(
        item.startswith("output:Jarvis: ") and "Teste para Jarvis" in item
        for item in operations
    )


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
    class CriticalCreateFileTool(CreateFileTool):
        risk = RiskLevel.CRITICAL

    denied = FixedConfirmation(False)
    executor = ToolExecutor(
        SafetyManager(denied), AuditLogger(tmp_path / "audit.log")
    )
    tool = CriticalCreateFileTool(FileSystemGuard(tmp_path))
    executor.execute(tool, path="negado.txt", content="x")
    assert not (tmp_path / "negado.txt").exists()
    allowed = FixedConfirmation(True)
    executor.safety_manager = SafetyManager(allowed)
    executor.execute(tool, path="permitido.txt", content="x")
    assert (tmp_path / "permitido.txt").exists()


def test_confirmation_timer_starts_after_playback_and_cooldown() -> None:
    operations: list[str] = []
    states: list[VoicePipelineState] = []
    manager = ResourceManager()
    recorder = SequenceRecorder([[1] * 3000], operations)
    confirmation = VoiceConfirmation(
        listener=FakeListener(operations),  # type: ignore[arg-type]
        recorder=recorder,  # type: ignore[arg-type]
        speech_to_text=FakeSTT("pode"),  # type: ignore[arg-type]
        text_to_speech=FakeAsyncTTS(operations),  # type: ignore[arg-type]
        resource_manager=manager,
        cooldown_waiter=lambda seconds: operations.append(
            f"cooldown:{seconds}"
        ),
        post_speech_cooldown_seconds=0.75,
        state_callback=states.append,
    )

    assert confirmation.confirm("edit_file", "README.md", RiskLevel.MEDIUM)

    assert operations.index("playback.wait") < operations.index("cooldown:0.75")
    assert operations.index("cooldown:0.75") < operations.index("capture:1")
    assert states[:2] == [
        VoicePipelineState.SPEAKING,
        VoicePipelineState.CONFIRMATION,
    ]


def test_first_timeout_reprompts_without_wake_and_preserves_request() -> None:
    operations: list[str] = []
    pending: list[ConfirmationRequest | None] = []
    manager = ResourceManager()
    first = SequenceRecorder([[]], operations)
    retry = SequenceRecorder([[1] * 3000], operations)
    tts = FakeAsyncTTS(operations)
    confirmation = VoiceConfirmation(
        listener=FakeListener(operations),  # type: ignore[arg-type]
        recorder=first,  # type: ignore[arg-type]
        retry_recorder=retry,  # type: ignore[arg-type]
        speech_to_text=FakeSTT("pode"),  # type: ignore[arg-type]
        text_to_speech=tts,  # type: ignore[arg-type]
        resource_manager=manager,
        cooldown_waiter=lambda _seconds: None,
    )
    first.pending = retry.pending = pending
    first.confirmation = retry.confirmation = confirmation

    assert confirmation.confirm(
        "obsidian_append_note", "Teste para Jarvis.md", RiskLevel.MEDIUM
    )

    assert first.calls == 1
    assert retry.calls == 1
    assert any("Ainda deseja" in text for text in tts.spoken)
    assert len(pending) == 2
    assert pending[0] is pending[1]
    assert pending[0] is not None
    assert pending[0].action == "obsidian_append_note"
    assert pending[0].target == "Teste para Jarvis.md"
    assert not any("Aguardando" in operation for operation in operations)


def test_second_timeout_is_distinct_from_denial_and_fails_closed() -> None:
    operations: list[str] = []
    first = SequenceRecorder([[]], operations)
    retry = SequenceRecorder([[]], operations)
    confirmation = VoiceConfirmation(
        listener=FakeListener(operations),  # type: ignore[arg-type]
        recorder=first,  # type: ignore[arg-type]
        retry_recorder=retry,  # type: ignore[arg-type]
        speech_to_text=FakeSTT("sim"),  # type: ignore[arg-type]
        text_to_speech=FakeAsyncTTS(operations),  # type: ignore[arg-type]
        resource_manager=ResourceManager(),
        cooldown_waiter=lambda _seconds: None,
    )
    request = ConfirmationRequest(
        "obsidian_append_note",
        "Teste para Jarvis.md",
        RiskLevel.MEDIUM,
        "Posso adicionar esse conteúdo à nota?",
    )

    result = confirmation.confirm_result(request)

    assert result.timed_out
    assert not result.denied
    assert not result.action_authorized
    assert first.calls == retry.calls == 1
    assert confirmation.pending_request is None
    assert any(
        "Não consegui confirmar. A ação não foi realizada." in text
        for text in confirmation.text_to_speech.spoken  # type: ignore[union-attr]
    )


def test_rejected_stt_gets_one_local_retry() -> None:
    operations: list[str] = []
    first = SequenceRecorder([[1] * 3000], operations)
    retry = SequenceRecorder([[1] * 3000], operations)
    stt = SequenceSTT([
        TranscriptionResult("ruído", "pt", 0.5, no_speech_probability=0.99),
        TranscriptionResult("pode fazer", "pt", 0.5),
    ])
    tts = FakeAsyncTTS(operations)
    confirmation = VoiceConfirmation(
        listener=FakeListener(operations),  # type: ignore[arg-type]
        recorder=first,  # type: ignore[arg-type]
        retry_recorder=retry,  # type: ignore[arg-type]
        speech_to_text=stt,  # type: ignore[arg-type]
        text_to_speech=tts,  # type: ignore[arg-type]
        resource_manager=ResourceManager(),
        cooldown_waiter=lambda _seconds: None,
    )

    assert confirmation.confirm("edit_file", "README.md", RiskLevel.MEDIUM)
    assert stt.calls == 2
    assert any("Não entendi sua confirmação" in text for text in tts.spoken)


def test_response_after_eight_seconds_is_inside_primary_window() -> None:
    operations: list[str] = []

    class DelayedRecorder:
        max_recording_seconds = 15.0
        elapsed_before_speech = 8.0

        def record(self) -> list[int]:
            operations.append("speech_after:8.0")
            assert self.elapsed_before_speech < self.max_recording_seconds
            return [1] * 3000

    confirmation = VoiceConfirmation(
        listener=FakeListener(operations),  # type: ignore[arg-type]
        recorder=DelayedRecorder(),  # type: ignore[arg-type]
        speech_to_text=FakeSTT("pode"),  # type: ignore[arg-type]
        text_to_speech=FakeAsyncTTS(operations),  # type: ignore[arg-type]
        resource_manager=ResourceManager(),
        cooldown_waiter=lambda _seconds: None,
    )

    assert confirmation.confirm(
        "obsidian_append_note",
        "Teste para Jarvis.md",
        RiskLevel.MEDIUM,
    )
    assert "speech_after:8.0" in operations
