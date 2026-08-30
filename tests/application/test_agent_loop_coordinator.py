from types import SimpleNamespace

from jarvis.application.agent_loop_coordinator import AgentLoopCoordinator
from jarvis.application.turn_policy import TurnKind
from jarvis.context.active import ContextRouter
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.risk import RiskLevel
from jarvis.tools.execution_result import ToolExecutionResult


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
    def system_context(_response_mode, user_message=None):
        return []

    @staticmethod
    def finalize_llm_response(
        content,
        _messages,
        *,
        tool_executed=False,
        successful_tool_names=(),
        allow_retry=True,
    ):
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

    result = coordinator.process("Olá", "text", TurnKind.EFFECT_REQUEST)

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

    result = coordinator.process(
        "Faça isso no computador", "text", TurnKind.EFFECT_REQUEST
    )

    assert result.startswith("Entendi que você quer uma ação real")
    assert "Pronto, fiz." not in result
    assert memory.get_history()[-1]["content"] == result


def test_agent_loop_returns_desktop_security_block_without_llm_rewrite():
    coordinator, memory, _calls = make_coordinator(action=True, response="")
    blocked = ToolExecutionResult(
        "Ação bloqueada por segurança: kill switch acionado.",
        "security_blocked",
        False,
    )

    result = coordinator._handle_execution_result(
        "Foca a janela do Obsidian.",
        SimpleNamespace(name="desktop_focus_window", risk=RiskLevel.MEDIUM),
        {"title": "Obsidian"},
        blocked,
        set(),
    )

    assert result == blocked.content
    assert memory.get_history()[-1]["content"] == blocked.content


def test_agent_loop_returns_desktop_verification_error_without_llm_rewrite():
    coordinator, memory, _calls = make_coordinator(action=True, response="")
    failed = ToolExecutionResult(
        'Erro ao executar a Tool: não foi possível verificar "calculator".',
        "error",
        True,
    )

    result = coordinator._handle_execution_result(
        "Abra a calculadora.",
        SimpleNamespace(
            name="desktop_open_application",
            risk=RiskLevel.MEDIUM,
        ),
        {"application": "calculator"},
        failed,
        set(),
    )

    assert result == failed.content
    assert memory.get_history()[-1]["content"] == failed.content


def test_agent_loop_returns_calendar_failure_without_llm_rewrite():
    coordinator, memory, _calls = make_coordinator(action=False, response="")
    failed = ToolExecutionResult(
        "Rede indisponível ao consultar Google Calendar.",
        "error",
        True,
    )

    result = coordinator._handle_execution_result(
        "Consulte minha agenda.",
        SimpleNamespace(
            name="google_calendar_list_events",
            risk=RiskLevel.LOW,
        ),
        {"period": "range:2026-09-01,2026-09-02"},
        failed,
        set(),
    )

    assert result == failed.content
    assert memory.get_history()[-1]["content"] == failed.content


def test_advisory_turn_uses_one_llm_call_without_tools_or_action_retry():
    coordinator, _memory, calls = make_coordinator(
        action=True,
        response="Eu priorizaria Vision antes de reconhecimento facial.",
    )

    result = coordinator.process(
        "Você acha que vale colocar reconhecimento facial agora?",
        "text",
        turn_kind=TurnKind.ADVISORY,
    )

    assert result.startswith("Eu priorizaria Vision")
    assert len(calls) == 1
    assert calls[0][1] == []
    assert any(
        message.get("role") == "system"
        and "turno é consultivo" in message.get("content", "")
        for message in calls[0][0]
    )


def test_advisory_turn_rejects_unexpected_tool_call_without_execution():
    coordinator, memory, calls = make_coordinator(action=True, response="")
    executions: list[str] = []
    coordinator.generate_llm = lambda messages, tools: (
        calls.append((list(messages), list(tools)))
        or LLMResponse(
            content="",
            tool_calls=[ToolCallRequest("create_file", {"path": "x.txt"})],
        )
    )
    coordinator.execute_tool = lambda *_args: executions.append("executed")

    result = coordinator.process(
        "Como eu poderia criar um arquivo?",
        "text",
        turn_kind=TurnKind.ADVISORY,
    )

    assert result.startswith("Este turno é consultivo")
    assert len(calls) == 1
    assert calls[0][1] == []
    assert executions == []
    assert memory.get_history()[-1]["content"] == result
