import pytest

from jarvis.application.state import AgentState, AgentStateBus
from jarvis.application.voice_bridge import VoiceStateBridge
from jarvis.voice.state import VoicePipelineState


@pytest.mark.parametrize(
    ("voice_state", "agent_state"),
    [
        (VoicePipelineState.WAITING_WAKE, AgentState.WAITING_WAKE),
        (VoicePipelineState.LISTENING, AgentState.LISTENING),
        (VoicePipelineState.PROCESSING, AgentState.PROCESSING),
        (VoicePipelineState.SPEAKING, AgentState.SPEAKING),
        (VoicePipelineState.WAITING_FOLLOWUP, AgentState.WAITING_FOLLOWUP),
        (VoicePipelineState.WAITING_REPAIR, AgentState.WAITING_REPAIR),
        (VoicePipelineState.CONFIRMATION, AgentState.CONFIRMATION),
    ],
)
def test_voice_bridge_maps_pipeline_state(
    voice_state: VoicePipelineState,
    agent_state: AgentState,
) -> None:
    bus = AgentStateBus()
    bridge = VoiceStateBridge(bus)

    bridge(voice_state)

    assert bus.snapshot.state is agent_state
    assert bus.snapshot.source == "voice_pipeline"
