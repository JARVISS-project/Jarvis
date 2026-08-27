from jarvis.application.state import AgentState
from jarvis.ui.hud_model import label_for, profile_for


def test_every_agent_state_has_a_hud_profile() -> None:
    for state in AgentState:
        profile = profile_for(state)
        assert profile.label
        assert profile.code
        assert 0.0 <= profile.activity <= 1.0
        assert 0.0 < profile.standby_opacity <= 1.0


def test_voice_states_enable_waveform() -> None:
    assert profile_for(AgentState.LISTENING).waveform is True
    assert profile_for(AgentState.SPEAKING).waveform is True
    assert profile_for(AgentState.PROCESSING).waveform is False


def test_waiting_wake_is_intentionally_quiet() -> None:
    standby = profile_for(AgentState.WAITING_WAKE)
    processing = profile_for(AgentState.PROCESSING)

    assert standby.activity < processing.activity
    assert standby.telemetry is False
    assert standby.standby_opacity < 1.0


def test_labels_are_exposed_without_importing_qt() -> None:
    assert label_for(AgentState.CONFIRMATION) == "CONFIRMATION"
    assert label_for(AgentState.ERROR) == "SYSTEM ALERT"
