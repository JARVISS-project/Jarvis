from jarvis.ui.layout import (
    HUD_WIDGET_SIZE,
    collapsed_window_size,
    expanded_window_width,
)


def test_hud_is_compact_for_ambient_use() -> None:
    assert HUD_WIDGET_SIZE == 212
    assert collapsed_window_size() == 220
    assert expanded_window_width() == 538
