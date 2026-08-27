from jarvis.context.compound_actions import CompoundActionPlanner


def test_open_and_read_plans_read_of_same_note() -> None:
    planner = CompoundActionPlanner()

    planned = planner.follow_up(
        "Abra Teste para Jarvis e leia ela.",
        completed_tool="obsidian_open_note",
        completed_arguments={"path": "Teste para Jarvis.md"},
    )

    assert planned is not None
    assert planned.name == "obsidian_read_note"
    assert planned.arguments == {"path": "Teste para Jarvis.md"}


def test_read_is_not_duplicated_or_inferred_when_not_requested() -> None:
    planner = CompoundActionPlanner()

    assert planner.follow_up(
        "Abra Teste para Jarvis.",
        completed_tool="obsidian_open_note",
        completed_arguments={"path": "Teste para Jarvis.md"},
    ) is None
    assert planner.follow_up(
        "Abra Teste para Jarvis e não leia.",
        completed_tool="obsidian_open_note",
        completed_arguments={"path": "Teste para Jarvis.md"},
    ) is None
    assert planner.follow_up(
        "Abra Teste para Jarvis e leia ela.",
        completed_tool="obsidian_open_note",
        completed_arguments={"path": "Teste para Jarvis.md"},
        already_requested={"obsidian_read_note"},
    ) is None
