"""The local Doctor route must never behave like a runtime command."""

from jarvis.runtime.resource_manager import ResourceManager
from jarvis.speech.base import TranscriptionResult
from jarvis.voice.state import VoicePipelineState
from tests.test_harness_runtime import RuntimeAwareLLM, make_harness
from tests.voice.test_pipeline import FakeTextToSpeech
from tests.voice.test_ptt_integration import build_pipeline, press, release


class EmptyFollowupRecorder:
    def __init__(self) -> None:
        self.calls = 0

    def record(self) -> list[int]:
        self.calls += 1
        return []


def arm_next_wake(pipeline) -> None:
    detected = False

    def detect(_frame) -> bool:
        nonlocal detected
        if detected:
            return False
        detected = True
        return True

    pipeline.listener.detector.process = detect
    pipeline.listener.cooldown_seconds = 0.0


def test_slash_doctor_via_ptt_speaks_summary_and_pipeline_is_reusable() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)
    tts = FakeTextToSpeech()
    followup = EmptyFollowupRecorder()
    states: list[VoicePipelineState] = []
    pipeline, audio = build_pipeline(
        harness=harness,
        text_to_speech=tts,
        conversation_followup_enabled=True,
        followup_recorder=followup,
        state_callback=states.append,
    )
    expected_summary = harness.doctor.voice_summary()
    pipeline.speech_to_text.result = TranscriptionResult("/doctor", "pt", 1.0)
    audio.on_read = lambda: release(pipeline) if audio.reads == 5 else None

    try:
        press(pipeline)
        assert pipeline.process_once()

        assert llm.calls == 0
        assert tts.spoken == [expected_summary]
        assert followup.calls == 0
        assert VoicePipelineState.WAITING_FOLLOWUP not in states
        assert pipeline.state is VoicePipelineState.WAITING_WAKE
        assert not pipeline._stop_requested.is_set()
        assert not pipeline.listener._closed

        pipeline.speech_to_text.result = TranscriptionResult("Olá", "pt", 1.0)
        audio.on_read = lambda: None
        arm_next_wake(pipeline)
        assert pipeline.process_once()

        assert llm.calls == 1
        assert tts.spoken[-1] == "Resposta"
        assert pipeline.state is VoicePipelineState.WAITING_WAKE
        assert not pipeline._stop_requested.is_set()
        assert not pipeline.listener._closed
    finally:
        pipeline.close()


def test_natural_doctor_via_wake_stays_local_and_keeps_followup() -> None:
    manager = ResourceManager()
    llm = RuntimeAwareLLM(manager)
    harness = make_harness(manager, llm)
    tts = FakeTextToSpeech()
    followup = EmptyFollowupRecorder()
    states: list[VoicePipelineState] = []
    pipeline, audio = build_pipeline(
        harness=harness,
        text_to_speech=tts,
        conversation_followup_enabled=True,
        followup_recorder=followup,
        state_callback=states.append,
    )
    expected_summary = harness.doctor.voice_summary()
    pipeline.speech_to_text.result = TranscriptionResult(
        "Me fala sobre o barra doctor.", "pt", 1.0,
    )
    audio.on_read = lambda: None
    arm_next_wake(pipeline)

    try:
        assert pipeline.process_once()

        assert llm.calls == 0
        assert tts.spoken == [expected_summary]
        assert followup.calls == 1
        assert VoicePipelineState.WAITING_FOLLOWUP in states
        assert pipeline.state is VoicePipelineState.WAITING_WAKE
        assert not pipeline._stop_requested.is_set()
        assert not pipeline.listener._closed
    finally:
        pipeline.close()
