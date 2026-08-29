from pathlib import Path

import pytest

from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import (
    DesktopApplication,
    DesktopWindow,
    WindowRectangle,
)
from jarvis.desktop.service import DesktopService


class FakeBackend:
    def __init__(
        self,
        windows: list[DesktopWindow] | None = None,
        running: list[bool] | None = None,
        visible: list[bool] | None = None,
    ) -> None:
        self.windows = windows or []
        self.focused: list[int] = []
        self.launched: list[Path] = []
        self.running = running or [True]
        self.visible = visible or [True]
        self.process_checks: list[tuple[str, ...]] = []
        self.window_checks: list[
            tuple[tuple[str, ...], tuple[str, ...]]
        ] = []
        self.pointer = (0, 0)
        self.pointer_report: tuple[int, int] | None = None
        self.escape_events: list[bool] = []
        self.foreground: int | None = None
        self.kill_on_escape_down: DesktopKillSwitch | None = None
        self.close_requests: list[int] = []
        self.close_immediately = True
        self.existing_handles = {window.handle for window in self.windows}
        self.list_calls = 0

    def list_windows(self) -> list[DesktopWindow]:
        self.list_calls += 1
        return list(self.windows)

    def inspect_window(self, handle: int) -> DesktopWindow | None:
        return next(
            (window for window in self.windows if window.handle == handle),
            None,
        )

    def focus_window(self, handle: int) -> None:
        self.focused.append(handle)
        self.foreground = handle

    def launch_application(self, executable: Path) -> None:
        self.launched.append(executable)

    def is_application_running(self, process_names: tuple[str, ...]) -> bool:
        self.process_checks.append(process_names)
        if len(self.running) > 1:
            return self.running.pop(0)
        return self.running[0]

    def has_application_window(
        self,
        process_names: tuple[str, ...],
        window_classes: tuple[str, ...],
    ) -> bool:
        self.window_checks.append((process_names, window_classes))
        if len(self.visible) > 1:
            return self.visible.pop(0)
        return self.visible[0]

    def window_rectangle(self, _handle: int) -> WindowRectangle:
        return WindowRectangle(10, 20, 110, 100)

    def window_capture_rectangle(self, handle: int) -> WindowRectangle:
        return self.window_rectangle(handle)

    def move_pointer(self, x: int, y: int) -> None:
        self.pointer = (x, y)

    def pointer_position(self) -> tuple[int, int]:
        return self.pointer_report or self.pointer

    def foreground_window(self) -> int | None:
        return self.foreground

    def set_escape_pressed(self, pressed: bool) -> None:
        self.escape_events.append(pressed)
        if pressed and self.kill_on_escape_down is not None:
            self.kill_on_escape_down.trip("teste entre key down e key up")

    def request_window_close(self, handle: int) -> None:
        self.close_requests.append(handle)
        if self.close_immediately:
            self.existing_handles.discard(handle)

    def window_exists(self, handle: int) -> bool:
        return handle in self.existing_handles


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
    assert backend.window_checks == [(("notepad.exe",), ())]
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

    with pytest.raises(RuntimeError, match="processo esperado não apareceu"):
        service.open_application("notepad")


def test_running_process_without_visible_window_is_not_success(
    tmp_path: Path,
) -> None:
    service, backend, _executable = build_service(tmp_path)
    backend.running = [True]
    backend.visible = [False]
    service.verification_timeout = 0.0

    with pytest.raises(RuntimeError, match="processo esperado apareceu"):
        service.open_application("notepad")

    assert backend.process_checks == [("notepad.exe",)]
    assert backend.window_checks == [(("notepad.exe",), ())]


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


def test_pointer_moves_only_to_verified_window_center(tmp_path: Path) -> None:
    service, backend, _executable = build_service(tmp_path)

    window = service.move_pointer_to_window_center("Obsidian")

    assert window.handle == 2
    assert backend.focused == [2]
    assert backend.pointer == (60, 60)


def test_pointer_move_fails_when_position_cannot_be_verified(
    tmp_path: Path,
) -> None:
    service, backend, _executable = build_service(tmp_path)
    backend.pointer_report = (0, 0)

    with pytest.raises(RuntimeError, match="verificar a posição"):
        service.move_pointer_to_window_center("Obsidian")


def test_escape_releases_key_if_kill_switch_trips_between_steps(
    tmp_path: Path,
) -> None:
    service, backend, _executable = build_service(tmp_path)
    backend.kill_on_escape_down = service.kill_switch

    with pytest.raises(PermissionError, match="kill switch"):
        service.press_escape("Obsidian")

    assert backend.focused == [2]
    assert backend.escape_events == [True, False]


def test_escape_verifies_foreground_window(tmp_path: Path) -> None:
    service, backend, _executable = build_service(tmp_path)

    window = service.press_escape("Obsidian")

    assert window.handle == 2
    assert backend.focused == [2]
    assert backend.escape_events == [True, False]


def test_close_window_reports_verified_absence(tmp_path: Path) -> None:
    service, backend, _executable = build_service(tmp_path)

    result = service.close_window("Obsidian")

    assert result.closed
    assert result.window.handle == 2
    assert backend.close_requests == [2]


def test_close_window_reports_pending_save_dialog_without_forcing(
    tmp_path: Path,
) -> None:
    service, backend, _executable = build_service(tmp_path)
    backend.close_immediately = False
    service.verification_timeout = 0.0

    result = service.close_window("Obsidian")

    assert not result.closed
    assert backend.close_requests == [2]
    assert backend.window_exists(2)


def test_prepared_action_does_not_resolve_title_again(tmp_path: Path) -> None:
    service, backend, _executable = build_service(tmp_path)

    prepared = service.prepare_window("Obsidian")
    service.move_pointer_to_prepared_window_center(prepared)

    assert backend.list_calls == 1
    assert backend.focused == [2]


def test_prepared_window_rejects_reused_handle_with_different_process(
    tmp_path: Path,
) -> None:
    service, backend, _executable = build_service(tmp_path)
    prepared = service.prepare_window("Obsidian")
    backend.windows[1] = DesktopWindow(2, "Obsidian", 999, None)

    with pytest.raises(RuntimeError, match="identidade"):
        service.focus_prepared_window(prepared)

    assert backend.focused == []


def test_prepared_window_repr_hides_native_identity_and_region(
    tmp_path: Path,
) -> None:
    service, _backend, _executable = build_service(tmp_path)

    prepared = service.prepare_window("Obsidian")

    assert repr(prepared) == "PreparedDesktopWindow(public_target='janela:Obsidian')"
    assert "200" not in repr(prepared)
