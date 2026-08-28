from pathlib import Path

import pytest

from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import DesktopApplication, DesktopWindow
from jarvis.desktop.service import DesktopService


class FakeBackend:
    def __init__(
        self,
        windows: list[DesktopWindow] | None = None,
        running: list[bool] | None = None,
    ) -> None:
        self.windows = windows or []
        self.focused: list[int] = []
        self.launched: list[Path] = []
        self.running = running or [True]
        self.process_checks: list[tuple[str, ...]] = []

    def list_windows(self) -> list[DesktopWindow]:
        return list(self.windows)

    def focus_window(self, handle: int) -> None:
        self.focused.append(handle)

    def launch_application(self, executable: Path) -> None:
        self.launched.append(executable)

    def is_application_running(self, process_names: tuple[str, ...]) -> bool:
        self.process_checks.append(process_names)
        if len(self.running) > 1:
            return self.running.pop(0)
        return self.running[0]


def build_service(tmp_path: Path) -> tuple[DesktopService, FakeBackend, Path]:
    executable = tmp_path / "notepad.exe"
    executable.write_bytes(b"fake")
    backend = FakeBackend(
        [
            DesktopWindow(1, "README.md - Visual Studio Code", 100),
            DesktopWindow(2, "Obsidian", 200),
        ]
    )
    catalog = DesktopApplicationCatalog(
        [DesktopApplication("notepad", executable)],
        {"bloco de notas": "notepad"},
    )
    service = DesktopService(backend, catalog, DesktopKillSwitch())
    return service, backend, executable


def test_window_resolution_prefers_exact_then_unique_partial(tmp_path: Path) -> None:
    service, _backend, _executable = build_service(tmp_path)

    assert service.resolve_window("Obsidian").handle == 2
    assert service.resolve_window("visual studio").handle == 1


def test_ambiguous_window_is_rejected_instead_of_guessing(tmp_path: Path) -> None:
    service, backend, _executable = build_service(tmp_path)
    backend.windows.extend(
        [
            DesktopWindow(3, "Projeto - Visual Studio Code"),
            DesktopWindow(4, "Outro - Visual Studio Code"),
        ]
    )

    with pytest.raises(ValueError, match="ambígua"):
        service.resolve_window("visual studio code")


def test_kill_switch_blocks_control_but_not_observation(tmp_path: Path) -> None:
    service, backend, _executable = build_service(tmp_path)
    service.kill_switch.trip("teste")

    assert len(service.list_windows()) == 2
    with pytest.raises(PermissionError, match="kill switch"):
        service.focus_window("Obsidian")
    with pytest.raises(PermissionError, match="kill switch"):
        service.open_application("notepad")
    assert backend.focused == []
    assert backend.launched == []


def test_application_launch_uses_only_catalog_path(tmp_path: Path) -> None:
    service, backend, executable = build_service(tmp_path)

    application = service.open_application("bloco de notas")

    assert application.name == "notepad"
    assert backend.launched == [executable]
    assert backend.process_checks == [("notepad.exe",)]
    with pytest.raises(ValueError, match="não está no catálogo"):
        service.open_application(str(tmp_path / "malware.exe"))


def test_application_launch_waits_for_verification(tmp_path: Path) -> None:
    service, backend, _executable = build_service(tmp_path)
    backend.running = [False, False, True]
    clock = iter([0.0, 0.1, 0.2])
    service.monotonic = lambda: next(clock)
    service.sleep = lambda _seconds: None

    service.open_application("notepad")

    assert len(backend.process_checks) == 3


def test_application_launch_fails_when_verification_times_out(
    tmp_path: Path,
) -> None:
    service, backend, _executable = build_service(tmp_path)
    backend.running = [False]
    service.verification_timeout = 0.0

    with pytest.raises(RuntimeError, match="Não foi possível verificar"):
        service.open_application("notepad")


def test_kill_switch_during_verification_blocks_only_future_control(
    tmp_path: Path,
) -> None:
    service, backend, _executable = build_service(tmp_path)
    backend.running = [False, True]
    service.sleep = lambda _seconds: service.kill_switch.trip("teste")

    application = service.open_application("notepad")

    assert application.name == "notepad"
    assert service.kill_switch.is_tripped
    with pytest.raises(PermissionError, match="kill switch"):
        service.open_application("notepad")
