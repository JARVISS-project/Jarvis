from typing import Any

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class TestTool(Tool):
    name = "test_tool"
    description = "Tool usada apenas para testar a arquitetura."
    risk = RiskLevel.SAFE

    def get_target(self, **kwargs: Any) -> str:
        return "nenhum"

    def execute(self, **kwargs: Any) -> str:
        return "Tool executada com sucesso."