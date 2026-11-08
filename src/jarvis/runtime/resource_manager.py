from datetime import datetime, timezone

from jarvis.runtime.state import RuntimeState


class ResourceManager:
    def __init__(self) -> None:
        self.state = RuntimeState.IDLE
        self.last_activity = datetime.now(timezone.utc)
        self._game_mode_enabled = False

    def activate(self) -> None:
        self.state = RuntimeState.ACTIVE
        self._touch()

    def confirmation(self) -> None:
        self.state = RuntimeState.CONFIRMATION
        self._touch()

    def conversation(self) -> None:
        self.state = RuntimeState.ACTIVE_CONVERSATION
        self._touch()

    def idle(self) -> None:
        self.state = (
            RuntimeState.GAME_MODE
            if self._game_mode_enabled
            else RuntimeState.IDLE
        )
        self._touch()

    def enable_game_mode(self) -> None:
        self._game_mode_enabled = True
        self.state = RuntimeState.GAME_MODE
        self._touch()

    def disable_game_mode(self) -> None:
        self._game_mode_enabled = False
        self.state = RuntimeState.IDLE
        self._touch()

    def is_game_mode_active(self) -> bool:
        return self._game_mode_enabled

    def heavy_features_allowed(self) -> bool:
        return (
            self.state is RuntimeState.ACTIVE
            and not self._game_mode_enabled
        )

    def _touch(self) -> None:
        self.last_activity = datetime.now(timezone.utc)
