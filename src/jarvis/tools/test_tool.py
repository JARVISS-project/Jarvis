from typing import Any

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class TestTool(Tool):
    name = "test_tool"
    description = "Tool usada apenas para testar a arquitetura."
    risk = RiskLevel.READ_ONLY

    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def get_target(self, **kwargs: Any) -> str:
        return "nenhum"

    def execute(self, **kwargs: Any) -> str:
        return "Tool executada com sucesso."