from datetime import datetime, timezone

from jarvis.activation.base import ActivationEvent, ActivationSource
from jarvis.activation.hotkey import HotkeyActivation


class FakeActivationSource(ActivationSource):
    def __init__(self, event: ActivationEvent) -> None:
        self.event = event

    def get_event(self) -> ActivationEvent | None:
        return self.event


def test_activation_source_can_be_replaced_by_fake() -> None:
    event = ActivationEvent(
        source="fake",
        timestamp=datetime.now(timezone.utc),
    )
    source: ActivationSource = FakeActivationSource(event)

    assert source.get_event() == event


def test_hotkey_stub_only_signals_activation() -> None:
    source = HotkeyActivation()

    assert source.shortcut == "Ctrl+Alt+J"
    assert source.get_event() is None

    source.signal()
    event = source.get_event()

    assert event is not None
    assert event.source == "hotkey"
    assert source.get_event() is None
