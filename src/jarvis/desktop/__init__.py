"""Capacidades locais de desktop com backend isolado e controle explícito."""

from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import DesktopApplication, DesktopWindow
from jarvis.desktop.service import DesktopService

__all__ = [
    "DesktopApplication",
    "DesktopKillSwitch",
    "DesktopService",
    "DesktopWindow",
]
