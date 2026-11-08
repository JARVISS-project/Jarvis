"""Métricas visuais independentes do Qt para a Ambient HUD."""

HUD_DESIGN_SIZE = 284
HUD_WIDGET_SIZE = 212
WINDOW_MARGIN = 4
PANEL_WIDTH = 316
WINDOW_SPACING = 2
EXPANDED_WINDOW_HEIGHT = 292


def collapsed_window_size() -> int:
    return HUD_WIDGET_SIZE + (WINDOW_MARGIN * 2)


def expanded_window_width() -> int:
    return collapsed_window_size() + WINDOW_SPACING + PANEL_WIDTH
