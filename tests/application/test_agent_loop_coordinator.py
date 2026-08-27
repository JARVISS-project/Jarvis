from types import SimpleNamespace

from jarvis.application.agent_loop_coordinator import AgentLoopCoordinator
from jarvis.context.active import ContextRouter
from jarvis.llm.types import LLMResponse
from jarvis.memory.conversation import ConversationMemory


class EmptyToolRegistry:
    def list_tools(self):
        return []

    def get(self, _name):
        return None


class EmptyToolSelection:
    def __init__(self, *, action: bool = False) -> None:
        self.action = action

    def select(self, _message, _context, _tools):
        return []

    def looks_like_action(self, _message, _context):
        return self.action


class ResponseStub:
    @staticmethod
    def tool_matches_context(_name, _active):
        return False

    @staticmethod
    def system_context(_response_mode):
        return []

    @staticmethod
    def finalize_llm_response(content, _messages, *, tool_executed=False):
        assert tool_executed is False
        return content


def make_coordinator(*, action: bool, response: str):
    memory = ConversationMemory()
    calls = []

    def generate(messages, tools):
        calls.append((list(messages), list(tools)))
        return LLMResponse(content=response)

    coordinator = AgentLoopCoordinator(
        config=SimpleNamespace(max_tool_iterations=3),
        llm=SimpleNamespace(),
        memory=memory,
        context_router=ContextRouter(),
        tool_registry=EmptyToolRegistry(),
        tool_selection_router=EmptyToolSelection(action=action),
        argument_validator=SimpleNamespace(),
        intent_guard=SimpleNamespace(),
        pending_actions=SimpleNamespace(),
        tool_execution_coordinator=SimpleNamespace(),
        response_coordinator=ResponseStub(),
        generate_llm=generate,
        execute_tool=lambda *_args: None,
    )
    return coordinator, memory, calls


def test_agent_loop_returns_grounded_conversational_response_without_tools():
    coordinator, memory, calls = make_coordinator(
        action=False,
        response="Resposta conversacional.",
    )

    result = coordinator.process("Olá")

    assert result == "Resposta conversacional."
    assert len(calls) == 1
    assert memory.get_history()[-1] == {
        "role": "assistant",
        "content": "Resposta conversacional.",
    }


def test_agent_loop_fails_safe_when_action_has_no_available_tool():
    coordinator, memory, _calls = make_coordinator(
        action=True,
        response="Pronto, fiz.",
    )

    result = coordinator.process("Faça isso no computador")

    assert result.startswith("Entendi que você quer uma ação real")
    assert "Pronto, fiz." not in result
    assert memory.get_history()[-1]["content"] == result
