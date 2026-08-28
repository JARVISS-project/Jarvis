from jarvis.application.state import AgentState, AgentStateBus
from jarvis.application.tool_execution_state import AgentToolExecutionObserver
from jarvis.tools.execution_result import ToolExecutionResult


def test_tool_execution_observer_publishes_executing_then_processing() -> None:
    bus = AgentStateBus(AgentState.PROCESSING)
    seen: list[AgentState] = []
    bus.subscribe(lambda snapshot: seen.append(snapshot.state), replay_current=False)
    observer = AgentToolExecutionObserver(bus)

    observer.started("read_file")
    observer.finished(
        "read_file",
        ToolExecutionResult("ok", "success", True),
    )

    assert seen == [AgentState.EXECUTING, AgentState.PROCESSING]


def test_tool_execution_observer_publishes_error_for_runtime_error() -> None:
    bus = AgentStateBus(AgentState.PROCESSING)
    observer = AgentToolExecutionObserver(bus)

    observer.started("desktop_focus_window")
    observer.finished(
        "desktop_focus_window",
        ToolExecutionResult("erro", "error", True),
    )

    assert bus.snapshot.state is AgentState.ERROR
