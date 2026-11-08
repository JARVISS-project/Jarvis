from __future__ import annotations

from typing import Any

from jarvis.media.models import PreparedMediaOperation
from jarvis.media.service import GsmtcMediaService
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


class CurrentMediaTool(Tool):
    name = "current_media"
    description = "Consulta a sessão de mídia atual indicada pelo Windows."
    risk = RiskLevel.READ_ONLY
    parameters = {"type": "object", "properties": {},
                  "additionalProperties": False}

    def __init__(self, service: GsmtcMediaService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        return "media:windows_current_session"

    def preflight(self, **kwargs: Any) -> None:
        if not self.service.available():
            raise RuntimeError("GSMTC indisponível.")

    def execute(self, **kwargs: Any) -> str:
        return self.service.describe()


class GsmtcMediaControlTool(Tool):
    name = "local_media_control"
    description = "Controla a sessão GSMTC atual sem toggle ou seleção arbitrária."
    risk = RiskLevel.REVERSIBLE_LOCAL
    parameters = {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": [
            "play", "pause", "next", "previous",
        ]}},
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, service: GsmtcMediaService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        return self.prepare(**kwargs).target

    def prepare(self, **kwargs: Any) -> ToolPreparation:
        prepared = self.service.prepare(str(kwargs["action"]))
        return ToolPreparation(
            "media:windows_current_session",
            prepared,
            self.service.binding_digest(prepared),
        )

    def preflight_prepared(
        self, preparation: ToolPreparation, **kwargs: Any
    ) -> None:
        self.service.revalidate(self._prepared(preparation))

    def refresh_prepared(
        self, preparation: ToolPreparation, **kwargs: Any
    ) -> ToolPreparation:
        self.preflight_prepared(preparation, **kwargs)
        return preparation

    def execute(self, **kwargs: Any) -> str:
        return self.execute_prepared(self.prepare(**kwargs), **kwargs)

    def execute_prepared(
        self, preparation: ToolPreparation, **kwargs: Any
    ) -> str:
        return self.service.execute(self._prepared(preparation))

    @staticmethod
    def _prepared(preparation: ToolPreparation) -> PreparedMediaOperation:
        if not isinstance(preparation.context, PreparedMediaOperation):
            raise RuntimeError("Preparação GSMTC ausente.")
        return preparation.context


def media_tools(service: GsmtcMediaService) -> list[Tool]:
    if not service.available():
        return []
    return [CurrentMediaTool(service), GsmtcMediaControlTool(service)]
