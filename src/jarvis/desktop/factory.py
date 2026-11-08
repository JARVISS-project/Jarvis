"""Construção do serviço de desktop sem espalhar checks de plataforma."""

import sys

from jarvis.desktop.catalog import windows_default_catalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.service import DesktopService


def build_desktop_service(
    kill_switch: DesktopKillSwitch,
) -> DesktopService | None:
    if sys.platform != "win32":
        return None
    from jarvis.desktop.windows import WindowsDesktopBackend

    return DesktopService(
        WindowsDesktopBackend(),
        windows_default_catalog(),
        kill_switch,
    )
