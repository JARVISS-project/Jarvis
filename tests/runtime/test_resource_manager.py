from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.state import RuntimeState


def test_initial_state_is_idle() -> None:
    manager = ResourceManager()

    assert manager.state is RuntimeState.IDLE
    assert manager.is_game_mode_active() is False
    assert manager.heavy_features_allowed() is False


def test_activate_changes_state_and_allows_heavy_features() -> None:
    manager = ResourceManager()
    previous_activity = manager.last_activity

    manager.activate()

    assert manager.state is RuntimeState.ACTIVE
    assert manager.heavy_features_allowed() is True
    assert manager.last_activity >= previous_activity


def test_idle_returns_to_idle() -> None:
    manager = ResourceManager()
    manager.activate()

    manager.idle()

    assert manager.state is RuntimeState.IDLE


def test_game_mode_remains_active_after_activity() -> None:
    manager = ResourceManager()

    manager.enable_game_mode()
    assert manager.state is RuntimeState.GAME_MODE
    assert manager.is_game_mode_active() is True
    assert manager.heavy_features_allowed() is False

    manager.activate()
    assert manager.state is RuntimeState.ACTIVE
    assert manager.heavy_features_allowed() is False

    manager.idle()
    assert manager.state is RuntimeState.GAME_MODE

    manager.disable_game_mode()
    assert manager.state is RuntimeState.IDLE
    assert manager.is_game_mode_active() is False
