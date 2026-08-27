from jarvis.context.active import ActiveContext
from jarvis.runtime.tool_selection import ToolSelectionRouter
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class NamedTool(Tool):
    description = "test"
    risk = RiskLevel.SAFE
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, name: str) -> None:
        self.name = name

    def get_target(self, **_kwargs):  # type: ignore[no-untyped-def]
        return "x"

    def execute(self, **_kwargs):  # type: ignore[no-untyped-def]
        return "x"


def names(tools):  # type: ignore[no-untyped-def]
    return [tool.name for tool in tools]


def test_plain_explanation_gets_no_tool_schemas() -> None:
    router = ToolSelectionRouter()
    tools = [NamedTool("read_file"), NamedTool("obsidian_read_note")]

    assert router.select(
        "Me explica o que é inteligência artificial.",
        ActiveContext.NONE,
        tools,
    ) == []


def test_obsidian_context_hides_filesystem_write_tools() -> None:
    router = ToolSelectionRouter()
    tools = [
        NamedTool("obsidian_read_note"),
        NamedTool("obsidian_append_note"),
        NamedTool("edit_file"),
        NamedTool("get_datetime"),
    ]

    selected = router.select("Leia ela", ActiveContext.OBSIDIAN, tools)

    assert names(selected) == [
        "obsidian_read_note",
        "obsidian_append_note",
        "get_datetime",
    ]


def test_colloquial_action_detection_uses_active_context() -> None:
    router = ToolSelectionRouter()

    assert router.looks_like_action(
        "bota Sabrina Linda nela",
        ActiveContext.OBSIDIAN,
    )
    assert router.looks_like_action(
        "testa alguma tool aí",
        ActiveContext.NONE,
    )
    assert not router.looks_like_action(
        "me explica como funciona uma ferramenta",
        ActiveContext.NONE,
    )
