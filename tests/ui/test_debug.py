import pytest

from jarvis.application.state import AgentState, AgentStateBus
from jarvis.ui.debug import AmbientUIDebugController


def test_debug_controller_cycles_states_without_runtime() -> None:
    bus = AgentStateBus()
    controller = AmbientUIDebugController(
        bus,
        states=(
            AgentState.WAITING_WAKE,
            AgentState.LISTENING,
            AgentState.PROCESSING,
        ),
    )

    assert controller.next_state() is AgentState.LISTENING
    assert controller.next_state() is AgentState.PROCESSING
    assert controller.next_state() is AgentState.WAITING_WAKE


def test_debug_controller_rejects_state_outside_debug_sequence() -> None:
    bus = AgentStateBus()
    controller = AmbientUIDebugController(
        bus,
        states=(AgentState.WAITING_WAKE, AgentState.LISTENING),
    )

    with pytest.raises(ValueError):
        controller.set_state(AgentState.ERROR)
