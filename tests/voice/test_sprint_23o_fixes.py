"""Lifecycle regressions found by the Sprint 23O human smoke."""

from concurrent.futures import ThreadPoolExecutor

from jarvis.voice.state import VoicePipelineState
from tests.voice.test_ptt_integration import (
    Playback,
    build_pipeline,
    press,
    release,
)


class SequenceRecorder:
    def __init__(self, recordings: list[list[int]]) -> None:
        self.recordings = recordings
        self.calls = 0

    def record(self) -> list[int]:
        self.calls += 1
        return self.recordings.pop(0)


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


def run_ptt_turn(pipeline, audio) -> None:
    release_at = audio.reads + 5
    audio.on_read = (
        lambda: release(pipeline) if audio.reads == release_at else None
    )
    press(pipeline)
    assert pipeline.process_once()
    audio.on_read = lambda: None


def test_repeated_ptt_and_wake_modes_keep_their_distinct_lifecycles() -> None:
    states: list[VoicePipelineState] = []
    followup = SequenceRecorder([[101], [], [202], []])
    pipeline, audio = build_pipeline(
        conversation_followup_enabled=True,
        followup_recorder=followup,
        state_callback=states.append,
    )
    try:
        run_ptt_turn(pipeline, audio)
        assert followup.calls == 0
        assert pipeline.state is VoicePipelineState.WAITING_WAKE

        arm_next_wake(pipeline)
        assert pipeline.process_once()
        assert followup.calls == 2
        assert pipeline.state is VoicePipelineState.WAITING_WAKE

        states_after_first_wake = len(states)
        run_ptt_turn(pipeline, audio)
        assert followup.calls == 2
        assert VoicePipelineState.WAITING_FOLLOWUP not in states[
            states_after_first_wake:
        ]

        arm_next_wake(pipeline)
        assert pipeline.process_once()
        assert followup.calls == 4
        assert len(pipeline.harness.calls) == 6
        assert states.count(VoicePipelineState.WAITING_FOLLOWUP) == 4
        assert pipeline.state is VoicePipelineState.WAITING_WAKE
    finally:
        pipeline.close()


def test_ptt_interrupts_wake_response_then_runs_a_new_single_turn() -> None:
    states: list[VoicePipelineState] = []
    followup = SequenceRecorder([[]])
    playback = Playback()
    pipeline, audio = build_pipeline(
        text_to_speech=playback,
        conversation_followup_enabled=True,
        followup_recorder=followup,
        state_callback=states.append,
    )
    arm_next_wake(pipeline)

    try:
        with ThreadPoolExecutor(1) as pool:
            wake_turn = pool.submit(pipeline.process_once)
            assert playback.started.wait(2)
            assert pipeline.state is VoicePipelineState.SPEAKING

            press(pipeline)
            assert playback.stopped.wait(2)
            assert wake_turn.result(2)

        assert pipeline.ptt.pending
        assert followup.calls == 0
        assert pipeline.state is VoicePipelineState.WAITING_WAKE

        pipeline.tts_enabled = False
        run_ptt_turn(pipeline, audio)

        assert len(pipeline.harness.calls) == 2
        assert followup.calls == 0
        assert pipeline.state is VoicePipelineState.WAITING_WAKE
        last_speaking = max(
            index for index, state in enumerate(states)
            if state is VoicePipelineState.SPEAKING
        )
        assert VoicePipelineState.LISTENING in states[last_speaking + 1:]
        assert VoicePipelineState.PROCESSING in states[last_speaking + 1:]
        assert VoicePipelineState.WAITING_FOLLOWUP not in states
    finally:
        pipeline.close()
