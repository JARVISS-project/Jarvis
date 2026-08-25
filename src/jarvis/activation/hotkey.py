from datetime import datetime, timezone

from jarvis.activation.base import ActivationEvent, ActivationSource


class HotkeyActivation(ActivationSource):
    DEFAULT_SHORTCUT = "Ctrl+Alt+J"

    def __init__(self, shortcut: str = DEFAULT_SHORTCUT) -> None:
        self.shortcut = shortcut
        self._pending_event: ActivationEvent | None = None

    def signal(self) -> None:
        self._pending_event = ActivationEvent(
            source="hotkey",
            timestamp=datetime.now(timezone.utc),
        )

    def get_event(self) -> ActivationEvent | None:
        event = self._pending_event
        self._pending_event = None
        return event
