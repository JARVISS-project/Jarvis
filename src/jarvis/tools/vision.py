"""Tools read-only para evidência local de mudança visual, OCR e UI tree."""

from typing import Any

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool
from jarvis.vision.models import CaptureRegion
from jarvis.vision.service import ScreenChange, VisionService


class VisionObserveScreenChangeTool(Tool):
    name = "vision_observe_screen_change"
    description = (
        "Captura a tela somente em memória e informa se ela mudou desde a "
        "observação anterior. Não retorna pixels, texto, screenshot ou hash."
    )
    risk = RiskLevel.SENSITIVE
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


class VisionExtractTextTool(Tool):
    name = "vision_extract_text"
    description = (
        "Extrai texto de uma região da tela usando OCR (read-only). "
        "Requer região explicitamente fornecida. Não persiste screenshot."
    )
    risk = RiskLevel.SENSITIVE
    parameters = {
        "type": "object",
        "properties": {
            "left": {"type": "integer", "description": "Coordenada esquerda da região"},
            "top": {"type": "integer", "description": "Coordenada superior da região"},
            "right": {"type": "integer", "description": "Coordenada direita da região"},
            "bottom": {"type": "integer", "description": "Coordenada inferior da região"},
            "language": {
                "type": "string",
                "description": "Idioma do OCR (ex: 'por', 'eng', 'por+eng')",
                "default": "por+eng",
            },
        },
        "required": ["left", "top", "right", "bottom"],
        "additionalProperties": False,
    }

    def __init__(self, service: VisionService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        region = CaptureRegion(
            kwargs["left"], kwargs["top"], kwargs["right"], kwargs["bottom"]
        )
        return f"desktop:screen:region:{region.left},{region.top},{region.right},{region.bottom}"

    def preflight(self, **kwargs: Any) -> None:
        self.service.capability_policy.require_tool(self.name)
        region = CaptureRegion(
            kwargs["left"], kwargs["top"], kwargs["right"], kwargs["bottom"]
        )
        if region.width <= 0 or region.height <= 0:
            raise ValueError("Região inválida: largura e altura devem ser positivas.")
        if region.width * region.height > self.service.max_pixels:
            raise ValueError("Região excede o limite de pixels.")

    def execute(self, **kwargs: Any) -> str:
        region = CaptureRegion(
            kwargs["left"], kwargs["top"], kwargs["right"], kwargs["bottom"]
        )
        language = kwargs.get("language", "por+eng")
        result = self.service.extract_text(region, language)
        return (
            f"Texto extraído (confiança {result.confidence:.1%}): {result.text or '(vazio)'}"
        )


class VisionGetUITreeTool(Tool):
    name = "vision_get_ui_tree"
    description = (
        "Captura a árvore de acessibilidade/UI de uma região (read-only). "
        "Não persiste screenshot. Requer Windows UI Automation."
    )
    risk = RiskLevel.SENSITIVE
    parameters = {
        "type": "object",
        "properties": {
            "left": {"type": "integer", "description": "Coordenada esquerda da região (opcional)"},
            "top": {"type": "integer", "description": "Coordenada superior da região (opcional)"},
            "right": {"type": "integer", "description": "Coordenada direita da região (opcional)"},
            "bottom": {"type": "integer", "description": "Coordenada inferior da região (opcional)"},
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, service: VisionService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        if all(k in kwargs for k in ("left", "top", "right", "bottom")):
            region = CaptureRegion(
                kwargs["left"], kwargs["top"], kwargs["right"], kwargs["bottom"]
            )
            return f"desktop:screen:uia:{region.left},{region.top},{region.right},{region.bottom}"
        return "desktop:screen:uia:full"

    def preflight(self, **kwargs: Any) -> None:
        self.service.capability_policy.require_tool(self.name)
        if all(k in kwargs for k in ("left", "top", "right", "bottom")):
            region = CaptureRegion(
                kwargs["left"], kwargs["top"], kwargs["right"], kwargs["bottom"]
            )
            if region.width <= 0 or region.height <= 0:
                raise ValueError("Região inválida: largura e altura devem ser positivas.")
            if region.width * region.height > self.service.max_pixels:
                raise ValueError("Região excede o limite de pixels.")

    def execute(self, **kwargs: Any) -> str:
        region = None
        if all(k in kwargs for k in ("left", "top", "right", "bottom")):
            region = CaptureRegion(
                kwargs["left"], kwargs["top"], kwargs["right"], kwargs["bottom"]
            )
        result = self.service.get_ui_tree(region)
        return self._format_ui_tree(result.root, 0)

    def _format_ui_tree(self, element, depth: int) -> str:
        indent = "  " * depth
        name_part = f' name="{element.name}"' if element.name else ""
        rect = element.bounding_rect
        lines = [
            f'{indent}{element.control_type}{name_part} '
            f'[{rect.left},{rect.top},{rect.right},{rect.bottom}]'
        ]
        for child in element.children:
            lines.append(self._format_ui_tree(child, depth + 1))
        return "\n".join(lines)
