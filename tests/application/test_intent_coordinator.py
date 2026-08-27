from types import SimpleNamespace

from jarvis.application.intent_coordinator import IntentCoordinator
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


def make_intent(*, game_mode=False, doctor=None, normal=None):
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
    normal = normal or (lambda message, mode: f"normal:{mode}:{message}")
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
        obsidian_coordinator=ObsidianStub(),
        tool_execution_coordinator=ToolExecutionStub(),
        response_coordinator=ResponseStub(),
        fast_path_response=lambda _message: None,
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
