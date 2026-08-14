from typing import Any

from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool


class ToolExecutor:
    def __init__(self, safety_manager: SafetyManager) -> None:
        self.safety_manager = safety_manager

    def execute(self, tool: Tool, **kwargs: Any) -> str:
        try:
            target = tool.get_target(**kwargs)

            authorized = self.safety_manager.authorize(
                action=tool.name,
                target=target,
                risk=tool.risk,
            )

            if not authorized:
                return "Ação não autorizada."

            return tool.execute(**kwargs)

        except PermissionError as error:
            return f"Ação bloqueada por segurança: {error}"

        except FileNotFoundError as error:
            return f"Arquivo ou diretório não encontrado: {error}"

        except Exception as error:
            return f"Erro ao executar a Tool '{tool.name}': {error}"