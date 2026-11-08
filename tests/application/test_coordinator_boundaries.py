from jarvis.application.response_coordinator import ResponseCoordinator
from jarvis.application.skill_coordinator import SkillCoordinator
from jarvis.application.tool_execution_coordinator import ToolExecutionCoordinator
from jarvis.context.active import ActiveContext


def test_skill_coordinator_preserves_natural_list_intent() -> None:
    assert SkillCoordinator.natural_skill_list_requested(
        "Me diz quais skills você tem."
    )
    assert not SkillCoordinator.natural_skill_list_requested(
        "Explique o que é uma skill em Python."
    )


def test_skill_coordinator_preserves_natural_learning_name() -> None:
    assert (
        SkillCoordinator.natural_skill_learning_name(
            "Aprenda esse procedimento como Organizar Nota"
        )
        == "Organizar Nota"
    )


def test_tool_execution_coordinator_detects_pending_target_field() -> None:
    assert ToolExecutionCoordinator.missing_target_field({"path": ""}) == "path"
    assert ToolExecutionCoordinator.missing_target_field({"target": None}) == "target"
    assert ToolExecutionCoordinator.missing_target_field({"query": "teste"}) is None


def test_response_coordinator_preserves_context_tool_boundaries() -> None:
    assert ResponseCoordinator.tool_matches_context(
        "obsidian_read_note", ActiveContext.OBSIDIAN
    )
    assert not ResponseCoordinator.tool_matches_context(
        "read_file", ActiveContext.OBSIDIAN
    )
    assert ResponseCoordinator.tool_matches_context(
        "read_file", ActiveContext.FILESYSTEM
    )
