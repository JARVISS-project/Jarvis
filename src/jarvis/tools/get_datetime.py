from collections.abc import Callable
from datetime import datetime
from typing import Any

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class GetDateTimeTool(Tool):
    name = "get_datetime"
    description = (
        "Obtém a data, a hora e o fuso horário locais atuais. "
        "Use para perguntas sobre hoje, horário ou data atual."
    )
    risk = RiskLevel.SAFE
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now().astimezone())

    def get_target(self, **kwargs: Any) -> str:
        return "relógio local"

    def execute(self, **kwargs: Any) -> str:
        current = self._clock()
        offset = current.strftime("%z")
        formatted_offset = (
            f"{offset[:3]}:{offset[3:]}" if offset else "local"
        )
        return (
            f"Data: {current:%d/%m/%Y}\n"
            f"Hora: {current:%H:%M}\n"
            f"Timezone: {formatted_offset}\n"
            f"ISO: {current.isoformat()}"
        )
