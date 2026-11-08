from typing import Any

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class DangerousTestTool(Tool):
    name = "dangerous_test"
    description = "Tool fictícia usada para testar ações de alto risco."
    risk = RiskLevel.SENSITIVE

    def get_target(self, **kwargs: Any) -> str:
        return "arquivo-ficticio.txt"

    def execute(self, **kwargs: Any) -> str:
        return "Ação perigosa fictícia executada."