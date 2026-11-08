"""Adaptação dos estados de voz para o estado observável da aplicação."""

from jarvis.application.state import AgentState, AgentStateBus
from jarvis.voice.state import VoicePipelineState


_VOICE_STATE_MAP = {
    VoicePipelineState.WAITING_WAKE: AgentState.WAITING_WAKE,
    VoicePipelineState.LISTENING: AgentState.LISTENING,
    VoicePipelineState.PROCESSING: AgentState.PROCESSING,
    VoicePipelineState.SPEAKING: AgentState.SPEAKING,
    VoicePipelineState.WAITING_FOLLOWUP: AgentState.WAITING_FOLLOWUP,
    VoicePipelineState.WAITING_REPAIR: AgentState.WAITING_REPAIR,
    VoicePipelineState.CONFIRMATION: AgentState.CONFIRMATION,
    VoicePipelineState.REQUEST_CONFIRMATION: AgentState.CONFIRMATION,
    VoicePipelineState.WAITING_CONFIRMATION: AgentState.CONFIRMATION,
    VoicePipelineState.CONFIRMED: AgentState.PROCESSING,
    VoicePipelineState.DENIED: AgentState.WAITING_WAKE,
    VoicePipelineState.EXPIRED: AgentState.WAITING_WAKE,
}


class VoiceStateBridge:
    """Callback conectável diretamente a ``VoicePipeline.state_callback``."""

    def __init__(self, state_bus: AgentStateBus) -> None:
        self.state_bus = state_bus

    def __call__(self, state: VoicePipelineState) -> None:
        self.state_bus.publish(
            _VOICE_STATE_MAP[state],
            source="voice_pipeline",
        )
