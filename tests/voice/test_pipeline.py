from datetime import datetime, timezone

from jarvis.activation.base import ActivationEvent
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.state import RuntimeState
from jarvis.speech.base import SpeechToText, TranscriptionResult
from jarvis.tts.base import TextToSpeech
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.pipeline import VoicePipeline


class FakeListener:
    def __init__(self, events: list[ActivationEvent | None | BaseException]) -> None:
        self.events = events
        self.started = False
        self.stopped = False
        self.closed = False
        self.operations: list[str] = []
        self.reset_calls = 0

    def start(self) -> None:
        self.started = True
        self.stopped = False
        self.operations.append("start")

    def stop(self) -> None:
        self.stopped = True
        self.started = False
        self.operations.append("stop")

    def reset(self) -> None:
        self.reset_calls += 1
        self.operations.append("reset")

    def get_event(self) -> ActivationEvent | None:
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self) -> None:
        self.closed = True
        self.operations.append("close")


class FakeRecorder:
    def __init__(self, audio: list[int]) -> None:
        self.audio = audio
        self.calls = 0

    def record(self) -> list[int]:
        self.calls += 1
        return self.audio


class FakeSpeechToText(SpeechToText):
    def __init__(
        self,
        result: TranscriptionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or TranscriptionResult("comando", "pt", 1.0)
        self.error = error
        self.calls: list[tuple[list[int], int]] = []
        self.closed = False

    def transcribe(
        self,
        audio: list[int],
        sample_rate: int,
    ) -> TranscriptionResult:
        self.calls.append((audio, sample_rate))
        if self.error is not None:
            raise self.error
        return self.result

    def close(self) -> None:
        self.closed = True


class FakeHarness:
    def __init__(self, response: str = "resposta") -> None:
        self.response = response
        self.calls: list[tuple[str, bool]] = []

    def processar_mensagem(
        self,
        mensagem: str,
        manage_runtime: bool = True,
    ) -> str:
        self.calls.append((mensagem, manage_runtime))
        return self.response


class FakeTextToSpeech(TextToSpeech):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.spoken: list[str] = []
        self.closed = False
        self.close_calls = 0
        self.on_speak: object | None = None

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        if callable(self.on_speak):
            self.on_speak()
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1


def activation() -> ActivationEvent:
    return ActivationEvent("wake_word", datetime.now(timezone.utc))


def make_pipeline(
    manager: ResourceManager,
    listener: FakeListener,
    recorder: FakeRecorder,
    stt: FakeSpeechToText,
    harness: FakeHarness,
    output: list[str],
    tts: FakeTextToSpeech | None = None,
    cooldown_waiter: object | None = None,
) -> VoicePipeline:
    return VoicePipeline(
        listener=listener,  # type: ignore[arg-type]
        recorder=recorder,  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=manager,
        text_to_speech=tts,
        output=output.append,
        cooldown_waiter=(
            cooldown_waiter
            if callable(cooldown_waiter)
            else lambda _seconds: None
        ),
    )


def test_waiting_for_wake_word_does_not_capture_or_load_stt() -> None:
    manager = ResourceManager()
    recorder = FakeRecorder([1])
    stt = FakeSpeechToText()
    harness = FakeHarness()
    pipeline = make_pipeline(
        manager, FakeListener([None]), recorder, stt, harness, []
    )

    assert pipeline.process_once() is False
    assert recorder.calls == 0
    assert stt.calls == []
    assert harness.calls == []
    assert manager.state is RuntimeState.IDLE


def test_waiting_idle_does_not_run_tts() -> None:
    tts = FakeTextToSpeech()
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([None]),
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness(),
        [],
        tts,
    )

    assert pipeline.process_once() is False
    assert tts.spoken == []


def test_wake_word_captures_audio_transcribes_and_delegates_to_harness() -> None:
    manager = ResourceManager()
    recorder = FakeRecorder([1, 2, 3])
    stt = FakeSpeechToText(
        TranscriptionResult("listar arquivos", "pt", 1.0)
    )
    harness = FakeHarness("arquivos listados")
    output: list[str] = []
    pipeline = make_pipeline(
        manager, FakeListener([activation()]), recorder, stt, harness, output
    )

    assert pipeline.process_once() is True
    assert recorder.calls == 1
    assert stt.calls == [([1, 2, 3], 16000)]
    assert harness.calls == [("listar arquivos", False)]
    assert "Ouvindo..." in output
    assert "Você: listar arquivos" in output
    assert "Jarvis: arquivos listados" in output
    assert manager.state is RuntimeState.IDLE


def test_empty_transcription_does_not_call_harness() -> None:
    manager = ResourceManager()
    stt = FakeSpeechToText(TranscriptionResult("   ", None, 0.5))
    harness = FakeHarness()
    pipeline = make_pipeline(
        manager,
        FakeListener([activation()]),
        FakeRecorder([1]),
        stt,
        harness,
        [],
    )

    pipeline.process_once()

    assert harness.calls == []
    assert manager.state is RuntimeState.IDLE


def test_empty_recording_does_not_load_stt_or_call_harness() -> None:
    manager = ResourceManager()
    stt = FakeSpeechToText()
    harness = FakeHarness()
    pipeline = make_pipeline(
        manager,
        FakeListener([activation()]),
        FakeRecorder([]),
        stt,
        harness,
        [],
    )

    pipeline.process_once()

    assert stt.calls == []
    assert harness.calls == []


def test_stt_exception_is_controlled_and_runtime_returns_to_idle() -> None:
    manager = ResourceManager()
    stt = FakeSpeechToText(error=RuntimeError("falha STT"))
    output: list[str] = []
    pipeline = make_pipeline(
        manager,
        FakeListener([activation()]),
        FakeRecorder([1]),
        stt,
        FakeHarness(),
        output,
    )

    pipeline.process_once()

    assert output[-1] == "Erro ao transcrever áudio: falha STT"
    assert manager.state is RuntimeState.IDLE


def test_game_mode_is_restored_after_voice_interaction() -> None:
    manager = ResourceManager()
    manager.enable_game_mode()
    pipeline = make_pipeline(
        manager,
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness(),
        [],
    )

    pipeline.process_once()

    assert manager.state is RuntimeState.GAME_MODE


def test_close_releases_listener_and_stt() -> None:
    manager = ResourceManager()
    listener = FakeListener([])
    stt = FakeSpeechToText()
    pipeline = make_pipeline(
        manager, listener, FakeRecorder([]), stt, FakeHarness(), []
    )

    pipeline.close()

    assert listener.closed is True
    assert stt.closed is True


def test_ctrl_c_closes_resources() -> None:
    manager = ResourceManager()
    listener = FakeListener([KeyboardInterrupt()])
    stt = FakeSpeechToText()
    output: list[str] = []
    tts = FakeTextToSpeech()
    pipeline = make_pipeline(
        manager,
        listener,
        FakeRecorder([]),
        stt,
        FakeHarness(),
        output,
        tts,
    )

    pipeline.run()

    assert listener.started is True
    assert listener.closed is True
    assert stt.closed is True
    assert tts.closed is True
    assert "Modo voice encerrado." in output


def test_final_response_is_printed_and_sent_to_tts() -> None:
    manager = ResourceManager()
    tts = FakeTextToSpeech()
    output: list[str] = []
    pipeline = make_pipeline(
        manager,
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness("**Resposta final**"),
        output,
        tts,
    )

    pipeline.process_once()

    assert "Jarvis: **Resposta final**" in output
    assert tts.spoken == ["Resposta final"]
    assert manager.state is RuntimeState.IDLE


def test_empty_final_response_does_not_call_tts() -> None:
    tts = FakeTextToSpeech()
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness("   "),
        [],
        tts,
    )

    pipeline.process_once()

    assert tts.spoken == []


def test_listener_is_suspended_during_tts_and_cooldown() -> None:
    listener = FakeListener([activation()])
    tts = FakeTextToSpeech()
    operations: list[str] = []
    tts.on_speak = lambda: operations.append(
        "speak_stopped" if listener.stopped else "speak_active"
    )

    def wait(seconds: float) -> None:
        operations.append(f"cooldown:{seconds}:{listener.stopped}")

    pipeline = make_pipeline(
        ResourceManager(),
        listener,
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness(),
        [],
        tts,
        wait,
    )

    pipeline.process_once()

    assert operations == ["speak_stopped", "cooldown:0.75:True"]
    assert listener.operations == ["stop", "reset", "start"]
    assert listener.started is True


def test_tts_error_is_controlled_and_wake_mode_resumes() -> None:
    manager = ResourceManager()
    listener = FakeListener([activation()])
    tts = FakeTextToSpeech(RuntimeError("falha SAPI"))
    output: list[str] = []
    pipeline = make_pipeline(
        manager,
        listener,
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness("resposta preservada"),
        output,
        tts,
    )

    pipeline.process_once()

    assert "Jarvis: resposta preservada" in output
    assert output[-2:] == [
        "Aviso: não foi possível reproduzir a resposta por voz.",
        "Falha no TTS: RuntimeError: falha SAPI",
    ]
    assert listener.started is True
    assert manager.state is RuntimeState.IDLE


def test_game_mode_allows_tts_and_is_restored() -> None:
    manager = ResourceManager()
    manager.enable_game_mode()
    tts = FakeTextToSpeech()
    pipeline = make_pipeline(
        manager,
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness("resposta"),
        [],
        tts,
    )

    pipeline.process_once()

    assert tts.spoken == ["resposta"]
    assert manager.state is RuntimeState.GAME_MODE


def test_close_releases_tts() -> None:
    tts = FakeTextToSpeech()
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([]),
        FakeRecorder([]),
        FakeSpeechToText(),
        FakeHarness(),
        [],
        tts,
    )

    pipeline.close()

    assert tts.closed is True


def test_only_final_harness_response_is_sent_to_tts() -> None:
    tts = FakeTextToSpeech()
    harness = FakeHarness("Resposta final ao usuário")
    harness.raw_tool_message = "RAW_TOOL_MESSAGE_INTERNAL"
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(),
        harness,
        [],
        tts,
    )

    pipeline.process_once()

    assert tts.spoken == ["Resposta final ao usuário"]
    assert harness.raw_tool_message not in tts.spoken


def test_thinking_is_removed_before_memory_terminal_and_tts() -> None:
    class ThinkingLLM(LLMClient):
        def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
            return LLMResponse(
                "raciocínio vazado\n</think>\nResposta limpa."
            )

    memory = ConversationMemory()
    harness = Harness(
        llm=ThinkingLLM(),
        tool_registry=ToolRegistry(),
        tool_executor=object(),  # type: ignore[arg-type]
        memory=memory,
    )
    tts = FakeTextToSpeech()
    output: list[str] = []
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(),
        harness,  # type: ignore[arg-type]
        output,
        tts,
    )

    pipeline.process_once()

    assert "Jarvis: Resposta limpa." in output
    assert tts.spoken == ["Resposta limpa."]
    assert memory.get_history()[-1] == {
        "role": "assistant",
        "content": "Resposta limpa.",
    }


def test_pipeline_reuses_tts_until_final_shutdown() -> None:
    listener = FakeListener(
        [activation(), activation(), KeyboardInterrupt()]
    )
    tts = FakeTextToSpeech()
    pipeline = make_pipeline(
        ResourceManager(),
        listener,
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness("resposta"),
        [],
        tts,
    )

    assert pipeline.process_once() is True
    assert tts.spoken == ["resposta"]
    assert tts.close_calls == 0

    assert pipeline.process_once() is True
    assert tts.spoken == ["resposta", "resposta"]
    assert tts.close_calls == 0

    pipeline.run()

    assert tts.close_calls == 1
    assert tts.closed is True
