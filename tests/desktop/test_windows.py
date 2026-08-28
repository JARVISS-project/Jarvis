from pathlib import Path
import sys

import pytest

from jarvis.desktop.windows import WindowsDesktopBackend


@pytest.mark.skipif(sys.platform != "win32", reason="backend exclusivo do Windows")
def test_windows_backend_observes_current_process() -> None:
    backend = WindowsDesktopBackend()

    assert backend.is_application_running((Path(sys.executable).name,))
    assert not backend.is_application_running(("jarvis_missing_process.exe",))
