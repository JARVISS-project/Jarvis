from pathlib import Path
import ctypes
import sys

import pytest

from jarvis.desktop.windows import WindowsDesktopBackend, _Input
from jarvis.desktop.models import DesktopWindow


@pytest.mark.skipif(sys.platform != "win32", reason="backend exclusivo do Windows")
def test_windows_backend_observes_current_process() -> None:
    backend = WindowsDesktopBackend()

    assert backend.is_application_running((Path(sys.executable).name,))
    assert not backend.is_application_running(("jarvis_missing_process.exe",))


def test_application_window_requires_allowed_win32_class() -> None:
    backend = object.__new__(WindowsDesktopBackend)
    backend._application_process_ids = lambda _names: {10}  # type: ignore[method-assign]
    backend.list_windows = lambda: [  # type: ignore[method-assign]
        DesktopWindow(1, "Program Manager", 10, "Progman"),
        DesktopWindow(2, "Início", 10, "CabinetWClass"),
    ]

    assert not backend.has_application_window(
        ("explorer.exe",),
        ("MissingClass",),
    )
    assert backend.has_application_window(
        ("explorer.exe",),
        ("CabinetWClass",),
    )


def test_send_input_structure_matches_native_windows_layout() -> None:
    expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28

    assert ctypes.sizeof(_Input) == expected_size
