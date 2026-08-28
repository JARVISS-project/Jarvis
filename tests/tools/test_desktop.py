from pathlib import Path

from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import DesktopApplication, DesktopWindow
from jarvis.desktop.service import DesktopService
from jarvis.security.risk import RiskLevel
from jarvis.security.audit import AuditLogger
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.desktop import (
    DesktopFocusWindowTool,
    DesktopListApplicationsTool,
    DesktopListWindowsTool,
    DesktopOpenApplicationTool,
)
from jarvis.tools.executor import ToolExecutor


class FakeBackend:
    def __init__(self) -> None:
        self.focused: list[int] = []
        self.launched: list[Path] = []

    def list_windows(self) -> list[DesktopWindow]:
        return [DesktopWindow(7, "Obsidian")]

    def focus_window(self, handle: int) -> None:
        self.focused.append(handle)

    def launch_application(self, executable: Path) -> None:
        self.launched.append(executable)

    def is_application_running(self, _process_names: tuple[str, ...]) -> bool:
        return True


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
    )


def test_desktop_tools_have_conservative_risks(tmp_path: Path) -> None:
    list_windows, list_apps, focus, open_app = build_tools(tmp_path)

    assert list_windows.risk is RiskLevel.LOW
    assert list_apps.risk is RiskLevel.SAFE
    assert focus.risk is RiskLevel.MEDIUM
    assert open_app.risk is RiskLevel.MEDIUM


def test_open_application_schema_exposes_only_allowlisted_names(tmp_path: Path) -> None:
    _list_windows, _list_apps, _focus, open_app = build_tools(tmp_path)

    assert open_app.parameters["properties"]["application"]["enum"] == [
        "notepad"
    ]


def test_desktop_tools_return_grounded_results(tmp_path: Path) -> None:
    list_windows, list_apps, focus, open_app = build_tools(tmp_path)

    assert "Obsidian" in list_windows.execute()
    assert "notepad" in list_apps.execute()
    assert "Obsidian" in focus.execute(title="Obsidian")
    result = open_app.execute(application="notepad")
    assert "notepad" in result
    assert "verificado em execução" in result


def test_kill_switch_returns_explicit_security_block_from_executor(
    tmp_path: Path,
) -> None:
    _list_windows, _list_apps, focus, _open_app = build_tools(tmp_path)
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
