import sys

from jarvis.desktop.control import DesktopKillSwitch
from jarvis.runtime.desktop_kill_switch import WindowsDesktopKillSwitchListener


def test_non_windows_listener_fails_closed() -> None:
    if sys.platform == "win32":
        return
    kill_switch = DesktopKillSwitch()
    listener = WindowsDesktopKillSwitchListener(kill_switch)

    assert not listener.start()
    assert kill_switch.is_tripped
