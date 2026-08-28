"""Observadores opcionais de execução sem acesso a argumentos sensíveis."""

from typing import Protocol

from jarvis.tools.execution_result import ToolExecutionResult


class ToolExecutionObserver(Protocol):
    def started(self, tool_name: str) -> None:
        """Chamado imediatamente antes da mutação real da Tool."""

    def finished(self, tool_name: str, result: ToolExecutionResult) -> None:
        """Chamado quando a tentativa de execução termina."""
