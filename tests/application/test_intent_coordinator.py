from types import SimpleNamespace

from jarvis.application.intent_coordinator import IntentCoordinator
from jarvis.application.turn_policy import TurnKind, TurnPolicy
from jarvis.context.active import ContextRouter


class SkillStub:
    @staticmethod
    def natural_skill_list_requested(_message):
        return False

    @staticmethod
    def natural_skill_run_name(_message):
        return None

    @staticmethod
    def natural_skill_learning_name(_message):
        return None

    @staticmethod
    def skill_control_fallback(_message):
        return None

    @staticmethod
    def learned_skill_response(_message):
        return None


class ObsidianStub:
    @staticmethod
    def available():
        return True

    @staticmethod
    def requests_write(_message):
        return False

    @staticmethod
    def write_available():
        return True

    @staticmethod
    def fast_response(_message):
        return None

    @staticmethod
    def compound_fast_response(_message):
        return None


class ResponseStub:
    @staticmethod
    def is_ambiguous_note_cleanup(_message):
        return False


class ToolExecutionStub:
    @staticmethod
    def process_pending_message(_message):
        return None

    @staticmethod
    def cancel_pending_action():
        raise AssertionError("não deveria cancelar")

    @staticmethod
    def process_pending_cancellation(_message):
        return None


def make_intent(
    *,
    game_mode=False,
    doctor=None,
    normal=None,
    obsidian=None,
    fast_path=None,
    tool_execution=None,
):
    llm = SimpleNamespace(keep_alive=None)

    def set_keep_alive(value):
        llm.keep_alive = value

    llm.set_keep_alive = set_keep_alive
    resource_manager = SimpleNamespace(
        is_game_mode_active=lambda: game_mode,
    )
    doctor = doctor or SimpleNamespace(
        voice_summary=lambda: "doctor voice",
        report=lambda: "doctor text",
    )
    normal = normal or (
        lambda message, mode, _kind: f"normal:{mode}:{message}"
    )
    coordinator = IntentCoordinator(
        config=SimpleNamespace(
            ollama_keep_alive_game_mode="30s",
            ollama_keep_alive_normal="5m",
        ),
        resource_manager=resource_manager,
        llm=llm,
        context_router=ContextRouter(),
        doctor=doctor,
        skill_coordinator=SkillStub(),
        obsidian_coordinator=obsidian or ObsidianStub(),
        tool_execution_coordinator=tool_execution or ToolExecutionStub(),
        response_coordinator=ResponseStub(),
        turn_policy=TurnPolicy(),
        fast_path_response=fast_path or (lambda _message: None),
        runtime_info_response=lambda _message: None,
        normal_message=normal,
        get_last_tool_trace=lambda: ("", []),
    )
    return coordinator, llm


def test_intent_coordinator_routes_natural_doctor_without_agent_loop():
    coordinator, llm = make_intent()

    result = coordinator.process("Como está teu doctor?", "voice")

    assert result == "doctor voice"
    assert llm.keep_alive is None


def test_intent_coordinator_delegates_to_agent_loop_with_keep_alive_policy():
    coordinator, llm = make_intent(game_mode=True)

    result = coordinator.process("Explique filas", "text")

    assert result == "normal:text:Explique filas"
    assert llm.keep_alive == "30s"


def test_desktop_window_request_is_not_blocked_by_obsidian_availability():
    obsidian = ObsidianStub()
    obsidian.available = lambda: False
    coordinator, _llm = make_intent(obsidian=obsidian)

    result = coordinator.process("Foca a janela do Obsidian.", "text")

    assert result == "normal:text:Foca a janela do Obsidian."


def test_advisory_turn_bypasses_operational_fast_paths() -> None:
    received: list[TurnKind] = []
    coordinator, llm = make_intent(
        normal=lambda _message, _mode, kind: received.append(kind) or "opinião",
        fast_path=lambda _message: "efeito executado",
    )

    result = coordinator.process(
        "Você acha que vale criar reconhecimento facial agora?"
    )

    assert result == "opinião"
    assert received == [TurnKind.ADVISORY]
    assert llm.keep_alive == "5m"


def test_advisory_turn_preserves_pending_action_unless_user_cancels() -> None:
    tool_execution = ToolExecutionStub()
    tool_execution.process_pending_cancellation = (
        lambda message: "Cancelado." if "cancela" in message.casefold() else None
    )
    coordinator, llm = make_intent(tool_execution=tool_execution)

    result = coordinator.process("O que você acha? Cancela isso.")

    assert result == "Cancelado."
    assert llm.keep_alive is None
