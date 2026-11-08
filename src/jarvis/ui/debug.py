"""Controles de simulação para desenvolver a Ambient UI sem microfone."""

from jarvis.application.state import AgentState, AgentStateBus


class AmbientUIDebugController:
    """Publica estados artificiais sem tocar no Core do Jarvis."""

    DEFAULT_SEQUENCE = (
        AgentState.WAITING_WAKE,
        AgentState.LISTENING,
        AgentState.PROCESSING,
        AgentState.SPEAKING,
        AgentState.WAITING_FOLLOWUP,
        AgentState.WAITING_REPAIR,
        AgentState.CONFIRMATION,
        AgentState.EXECUTING,
        AgentState.ERROR,
    )

    def __init__(
        self,
        state_bus: AgentStateBus,
        states: tuple[AgentState, ...] | None = None,
    ) -> None:
        self.state_bus = state_bus
        self.states = states or self.DEFAULT_SEQUENCE
        if not self.states:
            raise ValueError("A sequência de debug não pode ser vazia.")

    def set_state(self, state: AgentState) -> None:
        if state not in self.states:
            raise ValueError(f"Estado não disponível no debug: {state.value}")
        self.state_bus.publish(state, source="ui_debug")

    def next_state(self) -> AgentState:
        current = self.state_bus.snapshot.state
        try:
            index = self.states.index(current)
        except ValueError:
            index = -1
        next_state = self.states[(index + 1) % len(self.states)]
        self.set_state(next_state)
        return next_state
