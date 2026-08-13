from typing import Any

from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool


class ToolExecutor:
    def __init__(self, safety_manager: SafetyManager) -> None:
        self.safety_manager = safety_manager

    def execute(self, tool: Tool, **kwargs: Any) -> str:
        target = tool.get_target(**kwargs)

        authorized = self.safety_manager.authorize(
            action=tool.name,
            target=target,
            risk=tool.risk,
        )

        if not authorized:
            return "Execução bloqueada pela política de segurança."

        return tool.execute(**kwargs)