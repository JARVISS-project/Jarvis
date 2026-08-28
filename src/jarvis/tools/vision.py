"""Tool read-only para evidência local de mudança visual."""

from typing import Any

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool
from jarvis.vision.service import ScreenChange, VisionService


class VisionObserveScreenChangeTool(Tool):
    name = "vision_observe_screen_change"
    description = (
        "Captura a tela somente em memória e informa se ela mudou desde a "
        "observação anterior. Não retorna pixels, texto, screenshot ou hash."
    )
    risk = RiskLevel.MEDIUM
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, service: VisionService) -> None:
        self.service = service

    def get_target(self, **_kwargs: Any) -> str:
        return "desktop:screen"

    def preflight(self, **_kwargs: Any) -> None:
        self.service.capability_policy.require_tool(self.name)

    def execute(self, **_kwargs: Any) -> str:
        result = self.service.observe_change()
        status = {
            ScreenChange.BASELINE: "baseline local registrada",
            ScreenChange.CHANGED: "mudança visual detectada",
            ScreenChange.UNCHANGED: "nenhuma mudança visual detectada",
        }[result.status]
        return (
            f"Observação visual read-only concluída: {status}; "
            f"área observada {result.width}x{result.height}. "
            "Pixels não foram persistidos nem expostos."
        )
