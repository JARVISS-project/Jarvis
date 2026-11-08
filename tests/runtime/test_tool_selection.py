import pytest

from jarvis.context.active import ActiveContext
from jarvis.runtime.tool_selection import ToolSelectionRouter
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class NamedTool(Tool):
    description = "test"
    risk = RiskLevel.READ_ONLY
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


def test_desktop_request_selects_only_desktop_and_utility_tools() -> None:
    router = ToolSelectionRouter()
    tools = [
        NamedTool("desktop_list_windows"),
        NamedTool("desktop_focus_window"),
        NamedTool("read_file"),
        NamedTool("obsidian_read_note"),
        NamedTool("get_runtime_info"),
    ]

    selected = router.select(
        "Foque a janela do Obsidian no desktop.",
        ActiveContext.NONE,
        tools,
    )

    assert names(selected) == [
        "desktop_list_windows",
        "desktop_focus_window",
        "get_runtime_info",
    ]
    assert router.looks_like_action(
        "Foque a janela do Obsidian no desktop.", ActiveContext.NONE
    )


def test_window_request_overrides_obsidian_context() -> None:
    router = ToolSelectionRouter()
    tools = [
        NamedTool("desktop_focus_window"),
        NamedTool("obsidian_open_note"),
    ]

    selected = router.select(
        "Foca a janela do Obsidian.",
        ActiveContext.OBSIDIAN,
        tools,
    )

    assert names(selected) == ["desktop_focus_window"]


def test_note_request_mentioning_application_stays_in_obsidian() -> None:
    router = ToolSelectionRouter()
    tools = [
        NamedTool("desktop_open_application"),
        NamedTool("obsidian_open_note"),
    ]

    selected = router.select(
        "Abra a nota Projeto no aplicativo Obsidian.",
        ActiveContext.OBSIDIAN,
        tools,
    )

    assert names(selected) == ["obsidian_open_note"]


@pytest.mark.parametrize(
    ("message", "title"),
    [
        ("Foca a janela do Obsidian.", "obsidian"),
        ("Vai pro Obsidian.", "obsidian"),
        ("Coloca o VS Code em foco.", "vs code"),
        ("Traz a calculadora pra frente.", "calculadora"),
    ],
)
def test_natural_window_requests_route_to_desktop_focus(
    message: str,
    title: str,
) -> None:
    assert ToolSelectionRouter.desktop_action(message) == (
        "desktop_focus_window",
        {"title": title},
    )


def test_natural_application_open_routes_without_matching_note_requests() -> None:
    assert ToolSelectionRouter.desktop_action("Abra a calculadora.") == (
        "desktop_open_application",
        {"application": "calculadora"},
    )
    assert ToolSelectionRouter.desktop_action(
        "Abra a nota Projeto no aplicativo Obsidian."
    ) is None
    assert ToolSelectionRouter.desktop_action("Abra o bloco de notas.") == (
        "desktop_open_application",
        {"application": "bloco de notas"},
    )


@pytest.mark.parametrize(
    "message",
    [
        "Traz ela de volta.",
        "Coloca essa janela em foco.",
        "Foca nela.",
        "Volta pra ela.",
    ],
)
def test_desktop_reference_requires_bound_recent_window(message: str) -> None:
    assert ToolSelectionRouter.is_desktop_reference(message)
    assert ToolSelectionRouter.desktop_action(message) is None
    assert ToolSelectionRouter.desktop_action(message, "Obsidian") == (
        "desktop_focus_window",
        {"title": "Obsidian"},
    )


@pytest.mark.parametrize(
    "message",
    [
        "Mova o mouse para o centro da janela do Obsidian.",
        "Pressione Escape na janela do Obsidian.",
    ],
)
def test_limited_desktop_input_is_routed_as_effect_request(message: str) -> None:
    router = ToolSelectionRouter()
    tools = [
        NamedTool("desktop_move_pointer_to_window"),
        NamedTool("desktop_press_escape"),
        NamedTool("read_file"),
    ]

    assert names(router.select(message, ActiveContext.NONE, tools)) == [
        "desktop_move_pointer_to_window",
        "desktop_press_escape",
    ]
    assert router.looks_like_action(message, ActiveContext.NONE)


@pytest.mark.parametrize(
    ("message", "tool_name"),
    [
        (
            "Mova o mouse para o centro da janela do Obsidian.",
            "desktop_move_pointer_to_window",
        ),
        (
            "Movam o mouse para o centro da janela do Explorador de Arquivos.",
            "desktop_move_pointer_to_window",
        ),
        (
            "E mova o mouse para o centro da tela, no Explorador de Arquivos.",
            "desktop_move_pointer_to_window",
        ),
        (
            "Pressione Escape na janela do Obsidian.",
            "desktop_press_escape",
        ),
        (
            "Pressione o botão Escape do Bloco de notas.",
            "desktop_press_escape",
        ),
        (
            "Pressione o Escape de Bloco de notas.",
            "desktop_press_escape",
        ),
        (
            "Pressione o botão Escape da janela Bloco de notas.",
            "desktop_press_escape",
        ),
        (
            "Pressiona o Escape e da janela Bloco de notas.",
            "desktop_press_escape",
        ),
    ],
)
def test_limited_desktop_input_has_deterministic_tool_route(
    message: str,
    tool_name: str,
) -> None:
    action = ToolSelectionRouter.desktop_action(message)
    assert action is not None
    assert action[0] == tool_name
    assert action[1]["title"] in {
        "obsidian",
        "explorador de arquivos",
        "bloco de notas",
    }


def test_visual_change_request_exposes_only_vision_tool() -> None:
    router = ToolSelectionRouter()
    tools = [
        NamedTool("vision_observe_screen_change"),
        NamedTool("desktop_move_pointer_to_window"),
        NamedTool("read_file"),
    ]

    selected = router.select(
        "Verifique se a tela mudou.",
        ActiveContext.NONE,
        tools,
    )

    assert names(selected) == ["vision_observe_screen_change"]
    assert router.is_vision_request("Compare a tela com a observação anterior.")


def test_pointer_repeat_requires_bound_recent_window() -> None:
    assert ToolSelectionRouter.desktop_action("Mova novamente.") is None
    assert ToolSelectionRouter.desktop_action(
        "Mova novamente.",
        "Explorador de Arquivos",
    ) == (
        "desktop_move_pointer_to_window",
        {"title": "Explorador de Arquivos"},
    )


def test_escape_without_window_is_incomplete_desktop_input() -> None:
    assert ToolSelectionRouter.is_incomplete_desktop_input(
        "E pressione Escape."
    )
    assert not ToolSelectionRouter.is_incomplete_desktop_input(
        "Pressione Escape na janela do Obsidian."
    )


@pytest.mark.parametrize(
    ("message", "title"),
    [
        ("Feche o Bloco de notas.", "bloco de notas"),
        ("Fecha a janela do Obsidian.", "obsidian"),
        ("Encerre a janela do VS Code.", "vs code"),
    ],
)
def test_close_window_has_deterministic_route(message: str, title: str) -> None:
    assert ToolSelectionRouter.desktop_action(message) == (
        "desktop_close_window",
        {"title": title},
    )
