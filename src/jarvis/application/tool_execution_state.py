"""Publica execução real de Tools no barramento de estado da aplicação."""

from jarvis.application.state import AgentState, AgentStateBus
from jarvis.tools.execution_result import ToolExecutionResult


class AgentToolExecutionObserver:
    def __init__(self, state_bus: AgentStateBus) -> None:
        self.state_bus = state_bus

    def started(self, tool_name: str) -> None:
        self.state_bus.publish(
            AgentState.EXECUTING,
            source="tool_executor",
        )

    def finished(self, tool_name: str, result: ToolExecutionResult) -> None:
        state = (
            AgentState.ERROR
            if result.status == "error"
            else AgentState.PROCESSING
        )
        self.state_bus.publish(state, source="tool_executor")
