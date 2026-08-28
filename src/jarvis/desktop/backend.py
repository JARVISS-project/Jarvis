"""Contrato de baixo nível para backends de desktop."""

from pathlib import Path
from typing import Protocol

from jarvis.desktop.models import DesktopWindow


class DesktopBackend(Protocol):
    def list_windows(self) -> list[DesktopWindow]:
        """Retorna janelas de topo visíveis."""

    def focus_window(self, handle: int) -> None:
        """Traz uma janela existente para primeiro plano."""

    def launch_application(self, executable: Path) -> None:
        """Inicia um executável já validado pelo catálogo."""

    def is_application_running(self, process_names: tuple[str, ...]) -> bool:
        """Verifica processos usando identidades definidas pela allowlist."""
