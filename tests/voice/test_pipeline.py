from datetime import datetime, timezone

from jarvis.activation.base import ActivationEvent
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.state import RuntimeState
from jarvis.speech.base import SpeechToText, TranscriptionResult
from jarvis.speech.validator import TranscriptionValidator
from jarvis.tts.base import TextToSpeech
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.pipeline import VoicePipeline
from jarvis.voice.state import VoicePipelineState


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
        self.warm_up_calls = 0

    def transcribe(
        self,
        audio: list[int],
        sample_rate: int,
    ) -> TranscriptionResult:
        self.calls.append((audio, sample_rate))
        if self.error is not None:
            raise self.error
        return self.result

    def warm_up(self) -> None:
        self.warm_up_calls += 1

    def close(self) -> None:
        self.closed = True


class FakeHarness:
    def __init__(self, response: str = "resposta") -> None:
        self.response = response
        self.calls: list[tuple[str, bool]] = []
        self.response_modes: list[str] = []
        self.warm_up_calls = 0
        self.latency = {
            "harness_seconds": 0.20,
            "llm_seconds": 0.15,
            "llm_calls": 1,
            "tool_seconds": 0.01,
            "tool_calls": 1,
        }

    def get_last_latency(self) -> dict[str, float | int]:
        return dict(self.latency)

    def warm_up_llm(self) -> None:
        self.warm_up_calls += 1

    def processar_mensagem(
        self,
        mensagem: str,
        manage_runtime: bool = True,
        response_mode: str = "text",
    ) -> str:
        self.calls.append((mensagem, manage_runtime))
        self.response_modes.append(response_mode)
        return self.response


class FakeTextToSpeech(TextToSpeech):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.spoken: list[str] = []
        self.closed = False
        self.close_calls = 0
        self.stop_calls = 0
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

    def stop(self) -> None:
        self.stop_calls += 1


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
    assert harness.response_modes == ["voice"]
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


def test_false_wake_with_silence_is_inoffensive() -> None:
    output: list[str] = []
    stt = FakeSpeechToText()
    harness = FakeHarness()
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([activation()]),
        FakeRecorder([]),
        stt,
        harness,
        output,
    )

    assert pipeline.process_once() is True
    assert "Ação cancelada." not in output
    assert stt.calls == []
    assert harness.calls == []
    assert pipeline.state is VoicePipelineState.WAITING_WAKE


def test_rejected_transcription_never_runs_cancellation_interpreter() -> None:
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1] * 4000),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(
            TranscriptionResult(
                "cancela", "pt", 0.25, no_speech_probability=0.95
            )
        ),
        harness=FakeHarness(),
        resource_manager=ResourceManager(),
        transcription_validator=TranscriptionValidator(),
        output=output.append,
    )

    class FailIfCalled:
        def is_cancellation(self, _text: str) -> bool:
            raise AssertionError("rejeição não deve ser interpretada")

    pipeline.cancellation_interpreter = FailIfCalled()  # type: ignore[assignment]
    pipeline.process_once()
    assert "Ação cancelada." not in output


def test_low_confidence_cancel_like_transcript_does_not_cancel() -> None:
    harness = FakeHarness()
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1] * 4000),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(
            TranscriptionResult(
                "para",
                "pt",
                0.25,
                average_log_probability=-0.8,
                no_speech_probability=0.2,
                duration_after_vad_seconds=0.25,
            )
        ),
        harness=harness,
        resource_manager=ResourceManager(),
        transcription_validator=TranscriptionValidator(),
        output=output.append,
    )

    pipeline.process_once()
    assert "Ação cancelada." not in output
    assert harness.calls == []


def test_voice_debug_reports_safe_no_speech_metadata() -> None:
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness(),
        resource_manager=ResourceManager(),
        output=output.append,
        debug_enabled=True,
    )
    pipeline.process_once()
    assert any(line.endswith("wake detected") for line in output)
    assert any(line.endswith("recorder result=NO_SPEECH") for line in output)
    assert any("WAITING_WAKE" in line for line in output if "transition=" in line)


def test_general_command_retries_once_using_same_audio() -> None:
    class RetrySTT(FakeSpeechToText):
        def __init__(self) -> None:
            super().__init__()
            self.retry_audio: list[list[int]] = []

        def transcribe(self, audio, sample_rate):  # type: ignore[no-untyped-def]
            self.calls.append((audio, sample_rate))
            return TranscriptionResult(
                "Sela", "pt", 0.5, average_log_probability=-0.8,
                no_speech_probability=0.2, duration_after_vad_seconds=0.5,
            )

        def transcribe_retry(self, audio, sample_rate):  # type: ignore[no-untyped-def]
            self.retry_audio.append(audio)
            return TranscriptionResult(
                "selecione a nota", "pt", 0.5,
                average_log_probability=-0.3,
                no_speech_probability=0.1,
                duration_after_vad_seconds=0.5,
            )

    audio = [1] * 8000
    stt = RetrySTT()
    harness = FakeHarness()
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder(audio),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        transcription_validator=TranscriptionValidator(),
    )
    pipeline.process_once()
    assert len(stt.calls) == 1
    assert stt.retry_audio == [audio]
    assert harness.calls == [("selecione a nota", False)]


class QualitySequenceSTT(SpeechToText):
    def __init__(self, results: list[TranscriptionResult]) -> None:
        self.results = results
        self.audios: list[list[int]] = []

    def transcribe(self, audio, sample_rate):  # type: ignore[no-untyped-def]
        self.audios.append(audio)
        return self.results.pop(0)

    def transcribe_retry(self, audio, sample_rate):  # type: ignore[no-untyped-def]
        self.audios.append(audio)
        return self.results.pop(0)


def quality_result(text: str, logprob: float) -> TranscriptionResult:
    return TranscriptionResult(
        text, "pt", 1.0, average_log_probability=logprob,
        no_speech_probability=0.01, duration_after_vad_seconds=0.8,
    )


def test_divergent_general_command_enters_repair_without_new_wake() -> None:
    audio = [1] * 16000
    stt = QualitySequenceSTT(
        [
            quality_result("que adesão", -0.61),
            quality_result("que horas são", -0.60),
            quality_result("que horas são", -0.30),
        ]
    )
    harness = FakeHarness()
    repair = SequenceRecorder([audio])
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder(audio),  # type: ignore[arg-type]
        repair_recorder=repair,  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        transcription_validator=TranscriptionValidator(),
        output=output.append,
        debug_enabled=True,
    )
    pipeline.process_once()
    assert repair.calls == 1
    assert harness.calls == [("que horas são", False)]
    assert "Jarvis: Não entendi direito. Repete pra mim." in output
    assert any("WAITING_REPAIR" in line for line in output)


def test_second_failed_repair_returns_to_wake_without_loop() -> None:
    audio = [1] * 16000
    stt = QualitySequenceSTT(
        [
            quality_result("que adesão", -0.61),
            quality_result("que horas são", -0.60),
            quality_result("sela", -0.80),
            quality_result("sei lá", -0.79),
        ]
    )
    harness = FakeHarness()
    repair = SequenceRecorder([audio])
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder(audio),  # type: ignore[arg-type]
        repair_recorder=repair,  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        transcription_validator=TranscriptionValidator(),
        output=output.append,
    )
    pipeline.process_once()
    assert repair.calls == 1
    assert len(stt.audios) == 4
    assert harness.calls == []
    assert "Jarvis: Não consegui entender." in output
    assert pipeline.state is VoicePipelineState.WAITING_WAKE


def test_rejected_followup_uses_one_repair_and_continues_conversation() -> None:
    audio = [1] * 16000
    stt = QualitySequenceSTT(
        [
            quality_result("primeira pergunta", -0.30),
            quality_result("sela", -0.90),
            quality_result("a segunda", -0.88),
            quality_result("a segunda", -0.30),
        ]
    )
    harness = FakeHarness()
    repair = SequenceRecorder([audio])
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder(audio),  # type: ignore[arg-type]
        followup_recorder=SequenceRecorder([audio, []]),  # type: ignore[arg-type]
        repair_recorder=repair,  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        transcription_validator=TranscriptionValidator(),
        conversation_followup_enabled=True,
    )
    pipeline.process_once()
    assert repair.calls == 1
    assert [call[0] for call in harness.calls] == [
        "primeira pergunta", "a segunda"
    ]


def test_approved_cancellation_prints_the_understood_transcript() -> None:
    output: list[str] = []
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(TranscriptionResult("Cancela.", "pt", 0.5)),
        FakeHarness(),
        output,
    )
    pipeline.process_once()
    assert "Você [cancelamento]: Cancela." in output
    assert "Ação cancelada." in output


def test_context_switch_with_deixa_reaches_harness_instead_of_cancelling() -> None:
    text = "Agora deixa o Obsidian e procura por PyProject no projeto."
    harness = FakeHarness()
    output: list[str] = []
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(TranscriptionResult(text, "pt", 0.5)),
        harness,
        output,
    )

    pipeline.process_once()

    assert harness.calls == [(text, False)]
    assert not any("cancelada" in line.casefold() for line in output)


def test_one_hundred_false_wakes_never_reach_stt_or_harness() -> None:
    stt = FakeSpeechToText()
    harness = FakeHarness()
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()] * 100),  # type: ignore[arg-type]
        recorder=FakeRecorder([]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        output=output.append,
    )
    for _ in range(100):
        pipeline.process_once()
    assert stt.calls == []
    assert harness.calls == []
    assert "Ação cancelada." not in output
    assert not any(line.startswith("Você:") for line in output)


def test_one_hundred_reasoning_leaks_never_reach_output_tts_or_memory() -> None:
    class LeakRetryLLM(LLMClient):
        def __init__(self) -> None:
            self.generate_calls = 0
            self.retry_calls = 0

        def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
            self.generate_calls += 1
            return LLMResponse(content="Okay, the user asked... Wait, no...")

        def generate_final(self, messages):  # type: ignore[no-untyped-def]
            self.retry_calls += 1
            return LLMResponse(content="Resposta segura.")

    class UnusedExecutor:
        pass

    llm = LeakRetryLLM()
    memory = ConversationMemory(max_messages=300)
    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=UnusedExecutor(),  # type: ignore[arg-type]
        memory=memory,
    )
    tts = FakeTextToSpeech()
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()] * 100),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(
            TranscriptionResult("pergunta válida", "pt", 0.5)
        ),
        harness=harness,
        resource_manager=ResourceManager(),
        text_to_speech=tts,
        output=output.append,
        cooldown_waiter=lambda _seconds: None,
    )
    for _ in range(100):
        pipeline.process_once()
    exposed = " ".join(output + tts.spoken) + str(memory.get_history())
    assert "Okay, the user asked" not in exposed
    assert "Wait, no" not in exposed
    assert tts.spoken == ["Resposta segura."] * 100
    assert llm.generate_calls == 100
    assert llm.retry_calls == 100


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
                "Wait, no... The assistant should use tool_call.\n"
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
    assert "Wait, no" not in " ".join(output)
    assert "tool_call" not in " ".join(output)
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


class SequenceRecorder:
    def __init__(self, recordings: list[list[int]]) -> None:
        self.recordings = recordings
        self.calls = 0

    def record(self) -> list[int]:
        self.calls += 1
        return self.recordings.pop(0)


def test_followup_processes_second_message_without_new_wake_word() -> None:
    manager = ResourceManager()
    listener = FakeListener([activation()])
    initial = SequenceRecorder([[1]])
    followup = SequenceRecorder([[2], []])
    harness = FakeHarness("resposta")
    pipeline = VoicePipeline(
        listener=listener,  # type: ignore[arg-type]
        recorder=initial,  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=harness,
        resource_manager=manager,
        conversation_followup_enabled=True,
        followup_recorder=followup,  # type: ignore[arg-type]
    )

    assert pipeline.process_once() is True
    assert len(harness.calls) == 2
    assert listener.events == []
    assert initial.calls == 1
    assert followup.calls == 2
    assert manager.state is RuntimeState.IDLE


def test_followup_timeout_ends_conversation_without_running_stt() -> None:
    stt = FakeSpeechToText()
    followup = SequenceRecorder([[]])
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=FakeHarness(),
        resource_manager=ResourceManager(),
        conversation_followup_enabled=True,
        followup_recorder=followup,  # type: ignore[arg-type]
    )

    pipeline.process_once()
    assert len(stt.calls) == 1
    assert followup.calls == 1


def test_game_mode_uses_shorter_followup_recorder() -> None:
    manager = ResourceManager()
    manager.enable_game_mode()
    normal = SequenceRecorder([[2]])
    game = SequenceRecorder([[]])
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness(),
        resource_manager=manager,
        conversation_followup_enabled=True,
        followup_recorder=normal,  # type: ignore[arg-type]
        game_mode_followup_recorder=game,  # type: ignore[arg-type]
    )

    pipeline.process_once()
    assert normal.calls == 0
    assert game.calls == 1
    assert manager.state is RuntimeState.GAME_MODE


def test_cancellation_during_followup_never_reaches_harness() -> None:
    manager = ResourceManager()
    harness = FakeHarness()
    stt = FakeSpeechToText(TranscriptionResult("cancela", "pt", 0.5))
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=manager,
        conversation_followup_enabled=True,
        followup_recorder=SequenceRecorder([[2]]),  # type: ignore[arg-type]
    )
    pipeline.process_once()
    assert harness.calls == []
    assert manager.state is RuntimeState.IDLE


class SequenceSTT(SpeechToText):
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls = 0

    def transcribe(self, audio, sample_rate):  # type: ignore[no-untyped-def]
        self.calls += 1
        return TranscriptionResult(self.texts.pop(0), "pt", 0.5)


class FakeBargeIn:
    def __init__(self, recordings: list[list[int] | None]) -> None:
        self.recordings = recordings
        self.calls = 0

    def listen(self, playback):  # type: ignore[no-untyped-def]
        self.calls += 1
        recording = self.recordings.pop(0)
        if recording:
            playback.stop()
        return recording


def test_barge_in_cancellation_is_not_sent_to_harness() -> None:
    harness = FakeHarness("resposta longa")
    stt = SequenceSTT(["comando original", "deixa pra lá"])
    tts = FakeTextToSpeech()
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        text_to_speech=tts,
        barge_in_controller=FakeBargeIn([[2]]),  # type: ignore[arg-type]
        cooldown_waiter=lambda _seconds: None,
    )
    pipeline.process_once()
    assert harness.calls == [("comando original", False)]
    assert tts.stop_calls == 1
    assert stt.calls == 2
    assert pipeline.resource_manager.state is RuntimeState.IDLE


def test_barge_in_new_question_becomes_followup_after_stop() -> None:
    harness = FakeHarness("resposta")
    stt = SequenceSTT(["primeira pergunta", "que horas são"])
    barge = FakeBargeIn([[2], None])
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        text_to_speech=FakeTextToSpeech(),
        barge_in_controller=barge,  # type: ignore[arg-type]
        cooldown_waiter=lambda _seconds: None,
    )
    pipeline.process_once()
    assert harness.calls == [
        ("primeira pergunta", False),
        ("que horas são", False),
    ]
    assert stt.calls == 2
    assert barge.calls == 2


def test_safe_command_speaks_short_acknowledgement() -> None:
    tts = FakeTextToSpeech()
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(
            TranscriptionResult("liste os arquivos", "pt", 0.5)
        ),
        harness=FakeHarness("resultado"),
        resource_manager=ResourceManager(),
        text_to_speech=tts,
        acknowledgement_enabled=True,
        cooldown_waiter=lambda _seconds: None,
    )
    pipeline.process_once()
    assert tts.spoken == ["Certo.", "resultado"]


def test_expensive_tts_skips_short_acknowledgement() -> None:
    class ExpensiveTTS(FakeTextToSpeech):
        fast_acknowledgement = False

    tts = ExpensiveTTS()
    pipeline = make_pipeline(
        ResourceManager(),
        FakeListener([activation()]),
        FakeRecorder([1]),
        FakeSpeechToText(),
        FakeHarness("resultado"),
        [],
        tts=tts,
    )
    pipeline.acknowledgement_enabled = True

    pipeline.process_once()

    assert tts.spoken == ["resultado"]


def test_destructive_command_defers_acknowledgement_to_confirmation() -> None:
    tts = FakeTextToSpeech()
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(
            TranscriptionResult("apague teste.txt", "pt", 0.5)
        ),
        harness=FakeHarness("cancelado"),
        resource_manager=ResourceManager(),
        text_to_speech=tts,
        acknowledgement_enabled=True,
        cooldown_waiter=lambda _seconds: None,
    )
    pipeline.process_once()
    assert "Entendi." not in tts.spoken


class InspectingRecorder(SequenceRecorder):
    def __init__(self, recordings, callback):  # type: ignore[no-untyped-def]
        super().__init__(recordings)
        self.callback = callback

    def record(self) -> list[int]:
        self.callback()
        return super().record()


def test_followup_window_starts_only_after_tts_and_cooldown() -> None:
    operations: list[str] = []
    tts = FakeTextToSpeech()
    tts.on_speak = lambda: operations.append("tts_finished")
    pipeline: VoicePipeline
    followup = InspectingRecorder(
        [[]],
        lambda: operations.append(f"followup:{pipeline.state.value}"),
    )
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness("resposta longa"),
        resource_manager=ResourceManager(),
        text_to_speech=tts,
        conversation_followup_enabled=True,
        followup_recorder=followup,  # type: ignore[arg-type]
        cooldown_waiter=lambda _seconds: operations.append("cooldown_finished"),
    )
    pipeline.process_once()
    assert operations == [
        "tts_finished",
        "cooldown_finished",
        "followup:waiting_followup",
    ]


def test_long_tts_does_not_consume_followup_window_with_fake_clock() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

        def advance(self, seconds: float) -> None:
            self.now += seconds

    class TimedPlayback:
        interrupted = False

        def __init__(self, clock: FakeClock) -> None:
            self.clock = clock

        def wait(self) -> bool:
            self.clock.advance(25.0)
            return True

    class TimedTTS(FakeTextToSpeech):
        def __init__(self, clock: FakeClock) -> None:
            super().__init__()
            self.clock = clock

        def speak_async(self, text: str) -> TimedPlayback:
            self.spoken.append(text)
            return TimedPlayback(self.clock)

    class TimedRecorder(SequenceRecorder):
        def __init__(self, clock: FakeClock) -> None:
            super().__init__([[]])
            self.clock = clock

        def record(self) -> list[int]:
            self.clock.advance(8.0)
            return super().record()

    clock = FakeClock()
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness("resposta longa"),
        resource_manager=ResourceManager(),
        text_to_speech=TimedTTS(clock),
        conversation_followup_enabled=True,
        followup_recorder=TimedRecorder(clock),  # type: ignore[arg-type]
        cooldown_waiter=clock.advance,
        monotonic_clock=clock,
        debug_enabled=True,
        output=output.append,
    )

    pipeline.process_once()

    assert "[voice] t=0.000 tts started" in output
    assert "[voice] t=25.000 tts finished" in output
    assert (
        "[voice] t=25.750 followup window started "
        "timeout=8.0 reason=normal"
    ) in output
    assert "[voice] t=33.750 followup timeout expired" in output


def test_three_conversation_turns_need_only_one_wake_word() -> None:
    harness = FakeHarness("resposta")
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=SequenceSTT(["primeira", "segunda", "terceira"]),
        harness=harness,
        resource_manager=ResourceManager(),
        conversation_followup_enabled=True,
        followup_recorder=SequenceRecorder([[2], [3], []]),  # type: ignore[arg-type]
    )
    pipeline.process_once()
    assert [call[0] for call in harness.calls] == [
        "primeira", "segunda", "terceira"
    ]


def test_question_and_pending_action_use_longer_recorder() -> None:
    class PendingHarness(FakeHarness):
        def has_pending_action(self) -> bool:
            return True

    normal = SequenceRecorder([[2]])
    question = SequenceRecorder([[]])
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=PendingHarness("Qual nota você deseja abrir?"),
        resource_manager=ResourceManager(),
        conversation_followup_enabled=True,
        followup_recorder=normal,  # type: ignore[arg-type]
        question_followup_recorder=question,  # type: ignore[arg-type]
    )
    pipeline.process_once()
    assert question.calls == 1
    assert normal.calls == 0


def test_debug_identifies_question_followup_timeout() -> None:
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness("Deseja continuar?"),
        resource_manager=ResourceManager(),
        conversation_followup_enabled=True,
        followup_recorder=SequenceRecorder([[2]]),  # type: ignore[arg-type]
        question_followup_recorder=SequenceRecorder([[]]),  # type: ignore[arg-type]
        output=output.append,
        monotonic_clock=lambda: 10.0,
        debug_enabled=True,
    )

    pipeline.process_once()

    assert (
        "[voice] t=10.000 followup window started "
        "timeout=15.0 reason=assistant_question"
    ) in output


def test_natural_conversation_end_skips_llm_and_followup() -> None:
    harness = FakeHarness("resposta")
    stt = SequenceSTT(["pergunta", "beleza valeu"])
    followup = SequenceRecorder([[2], [3]])
    tts = FakeTextToSpeech()
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=SequenceRecorder([[1]]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        text_to_speech=tts,
        conversation_followup_enabled=True,
        followup_recorder=followup,  # type: ignore[arg-type]
        cooldown_waiter=lambda _seconds: None,
    )
    pipeline.process_once()
    assert harness.calls == [("pergunta", False)]
    assert followup.calls == 1
    assert tts.spoken[-1] == "Por nada. Até mais."


def test_waiting_wake_is_not_printed_during_speech() -> None:
    output: list[str] = []
    tts = FakeTextToSpeech()
    observed_counts: list[int] = []
    tts.on_speak = lambda: observed_counts.append(
        output.count('Aguardando "Jarvis"...')
    )
    pipeline = VoicePipeline(
        listener=FakeListener([activation(), KeyboardInterrupt()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness(),
        resource_manager=ResourceManager(),
        text_to_speech=tts,
        output=output.append,
        cooldown_waiter=lambda _seconds: None,
    )
    pipeline.run()
    assert observed_counts == [1]




def test_run_warms_stt_before_announcing_waiting_wake() -> None:
    output: list[str] = []
    stt = FakeSpeechToText()
    listener = FakeListener([KeyboardInterrupt()])
    pipeline = VoicePipeline(
        listener=listener,  # type: ignore[arg-type]
        recorder=FakeRecorder([]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=FakeHarness(),
        resource_manager=ResourceManager(),
        warm_up_stt_on_start=True,
        output=output.append,
    )

    pipeline.run()

    assert stt.warm_up_calls == 1
    assert output[:3] == [
        "Preparando reconhecimento de voz...",
        "Reconhecimento de voz pronto.",
        'Aguardando "Jarvis"...',
    ]

def test_latency_debug_outputs_compact_turn_breakdown() -> None:
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1, 2, 3]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness(),
        resource_manager=ResourceManager(),
        text_to_speech=FakeTextToSpeech(),
        output=output.append,
        cooldown_waiter=lambda _seconds: None,
        latency_debug_enabled=True,
    )

    assert pipeline.process_once() is True

    latency_lines = [line for line in output if line.startswith("[latency]")]
    assert len(latency_lines) == 1
    line = latency_lines[0]
    assert "STT=" in line
    assert "Harness=0.20s" in line
    assert "LLM=0.15s/1" in line
    assert "Tools=0.01s/1" in line
    assert "TTS-total=" in line


def test_voice_run_warms_stt_and_llm_before_waiting_for_wake() -> None:
    output: list[str] = []
    stt = FakeSpeechToText()
    harness = FakeHarness()
    pipeline = VoicePipeline(
        listener=FakeListener([KeyboardInterrupt()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=stt,
        harness=harness,
        resource_manager=ResourceManager(),
        output=output.append,
        warm_up_stt_on_start=True,
        warm_up_llm_on_start=True,
    )

    pipeline.run()

    assert stt.warm_up_calls == 1
    assert harness.warm_up_calls == 1
    assert output.index("Reconhecimento de voz pronto.") < output.index(
        "Assistente pronto."
    )
    assert output.index("Assistente pronto.") < output.index(
        'Aguardando "Jarvis"...'
    )


def test_wake_acknowledgement_is_spoken_before_capture_when_enabled() -> None:
    manager = ResourceManager()
    tts = FakeTextToSpeech()
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(
            TranscriptionResult("que horas são", "pt", 0.5)
        ),
        harness=FakeHarness("agora são treze horas"),
        resource_manager=manager,
        text_to_speech=tts,
        wake_acknowledgement_enabled=True,
        output=output.append,
        cooldown_waiter=lambda _seconds: None,
    )

    pipeline.process_once()

    assert tts.spoken == ["Oi, tô aqui.", "agora são treze horas"]
    assert "Jarvis: Oi, tô aqui." in output


def test_wake_acknowledgement_rotates_without_llm() -> None:
    tts = FakeTextToSpeech()
    pipeline = VoicePipeline(
        listener=FakeListener([activation(), activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness("ok"),
        resource_manager=ResourceManager(),
        text_to_speech=tts,
        wake_acknowledgement_enabled=True,
        cooldown_waiter=lambda _seconds: None,
    )

    pipeline.process_once()
    pipeline.process_once()

    assert tts.spoken[0] == "Oi, tô aqui."
    assert tts.spoken[2] == "Diga."


def test_state_callback_exposes_voice_states_for_future_ui() -> None:
    states: list[VoicePipelineState] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=FakeHarness("ok"),
        resource_manager=ResourceManager(),
        state_callback=states.append,
        cooldown_waiter=lambda _seconds: None,
    )

    pipeline.process_once()

    assert VoicePipelineState.LISTENING in states
    assert VoicePipelineState.PROCESSING in states


def test_latency_formats_fast_tool_time_in_milliseconds() -> None:
    harness = FakeHarness("ok")
    harness.latency["tool_seconds"] = 0.0008
    harness.latency["tool_calls"] = 1
    output: list[str] = []
    pipeline = VoicePipeline(
        listener=FakeListener([activation()]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=harness,
        resource_manager=ResourceManager(),
        output=output.append,
        latency_debug_enabled=True,
        cooldown_waiter=lambda _seconds: None,
    )

    pipeline.process_once()

    assert any("Tools=0.8ms/1" in line for line in output)
