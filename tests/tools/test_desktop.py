from pathlib import Path

from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import (
    DesktopApplication,
    DesktopWindow,
    WindowRectangle,
)
from jarvis.desktop.service import DesktopService
from jarvis.security.risk import RiskLevel
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.desktop import (
    DesktopFocusWindowTool,
    DesktopCloseWindowTool,
    DesktopListApplicationsTool,
    DesktopListWindowsTool,
    DesktopMovePointerToWindowTool,
    DesktopOpenApplicationTool,
    DesktopPressEscapeTool,
)
from jarvis.tools.executor import ToolExecutor


class FakeBackend:
    def __init__(self) -> None:
        self.focused: list[int] = []
        self.launched: list[Path] = []
        self.pointer = (0, 0)
        self.escape_events: list[bool] = []
        self.foreground: int | None = None
        self.close_requests: list[int] = []
        self.existing_handles = {1}

    def list_windows(self) -> list[DesktopWindow]:
        return [DesktopWindow(7, "Obsidian")]

    def focus_window(self, handle: int) -> None:
        self.focused.append(handle)
        self.foreground = handle

    def launch_application(self, executable: Path) -> None:
        self.launched.append(executable)

    def is_application_running(self, _process_names: tuple[str, ...]) -> bool:
        return True

    def has_application_window(
        self,
        _process_names: tuple[str, ...],
        _window_classes: tuple[str, ...],
    ) -> bool:
        return True

    def window_rectangle(self, _handle: int) -> WindowRectangle:
        return WindowRectangle(0, 0, 100, 80)

    def move_pointer(self, x: int, y: int) -> None:
        self.pointer = (x, y)

    def pointer_position(self) -> tuple[int, int]:
        return self.pointer

    def foreground_window(self) -> int | None:
        return self.foreground

    def set_escape_pressed(self, pressed: bool) -> None:
        self.escape_events.append(pressed)

    def request_window_close(self, handle: int) -> None:
        self.close_requests.append(handle)
        self.existing_handles.discard(handle)

    def window_exists(self, handle: int) -> bool:
        return handle in self.existing_handles


def build_tools(tmp_path: Path):  # type: ignore[no-untyped-def]
    executable = tmp_path / "notepad.exe"
    executable.write_bytes(b"fake")
    service = DesktopService(
        FakeBackend(),
        DesktopApplicationCatalog(
            [DesktopApplication("notepad", executable)]
        ),
        DesktopKillSwitch(),
    )
    return (
        DesktopListWindowsTool(service),
        DesktopListApplicationsTool(service),
        DesktopFocusWindowTool(service),
        DesktopOpenApplicationTool(service),
        DesktopMovePointerToWindowTool(service),
        DesktopPressEscapeTool(service),
        DesktopCloseWindowTool(service),
    )


def test_desktop_tools_have_conservative_risks(tmp_path: Path) -> None:
    list_windows, list_apps, focus, open_app, move_pointer, press_escape, close = (
        build_tools(tmp_path)
    )

    assert list_windows.risk is RiskLevel.LOW
    assert list_apps.risk is RiskLevel.SAFE
    assert focus.risk is RiskLevel.MEDIUM
    assert open_app.risk is RiskLevel.MEDIUM
    assert move_pointer.risk is RiskLevel.MEDIUM
    assert press_escape.risk is RiskLevel.MEDIUM
    assert close.risk is RiskLevel.HIGH


def test_open_application_schema_exposes_only_allowlisted_names(tmp_path: Path) -> None:
    _list_windows, _list_apps, _focus, open_app, _move, _escape, _close = (
        build_tools(tmp_path)
    )

    assert open_app.parameters["properties"]["application"]["enum"] == [
        "notepad"
    ]


def test_limited_input_schemas_expose_no_coordinates_or_arbitrary_keys(
    tmp_path: Path,
) -> None:
    _list, _apps, _focus, _open, move_pointer, press_escape, close = (
        build_tools(tmp_path)
    )

    assert set(move_pointer.parameters["properties"]) == {"title"}
    assert set(press_escape.parameters["properties"]) == {"title"}
    assert set(close.parameters["properties"]) == {"title"}


def test_desktop_tools_return_grounded_results(tmp_path: Path) -> None:
    list_windows, list_apps, focus, open_app, move_pointer, press_escape, close = (
        build_tools(tmp_path)
    )

    assert "Obsidian" in list_windows.execute()
    assert "notepad" in list_apps.execute()
    assert "Obsidian" in focus.execute(title="Obsidian")
    result = open_app.execute(application="notepad")
    assert "notepad" in result
    assert "janela visível" in result
    assert "posição verificada" in move_pointer.execute(title="Obsidian")
    assert "primeiro plano verificada" in press_escape.execute(
        title="Obsidian"
    )
    assert "ausência verificada" in close.execute(title="Obsidian")


def test_kill_switch_returns_explicit_security_block_from_executor(
    tmp_path: Path,
) -> None:
    _list_windows, _list_apps, focus, _open_app, _move, _escape, _close = (
        build_tools(tmp_path)
    )
    focus.service.kill_switch.trip("teste real")
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
    )

    result = executor.execute_with_result(focus, title="janela inexistente")

    assert result.status == "security_blocked"
    assert "kill switch" in result.content
    assert "teste real" in result.content
    assert focus.service.backend.focused == []


def test_disabled_desktop_control_blocks_limited_input_before_effect(
    tmp_path: Path,
) -> None:
    _list, _apps, _focus, _open, move_pointer, press_escape, close = (
        build_tools(tmp_path)
    )
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
        capability_policy=CapabilityPolicy(
            {Capability.DESKTOP_CONTROL: False}
        ),
    )

    move_result = executor.execute_with_result(
        move_pointer,
        title="Obsidian",
    )
    escape_result = executor.execute_with_result(
        press_escape,
        title="Obsidian",
    )
    close_result = executor.execute_with_result(
        close,
        title="Obsidian",
    )

    assert move_result.status == "security_blocked"
    assert escape_result.status == "security_blocked"
    assert close_result.status == "security_blocked"
    assert move_pointer.service.backend.pointer == (0, 0)
    assert move_pointer.service.backend.escape_events == []
    assert move_pointer.service.backend.close_requests == []
