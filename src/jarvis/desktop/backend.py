"""Contrato de baixo nível para backends de desktop."""

from pathlib import Path
from typing import Protocol

from jarvis.desktop.models import DesktopWindow, WindowRectangle


class DesktopBackend(Protocol):
    def list_windows(self) -> list[DesktopWindow]:
        """Retorna janelas de topo visíveis."""

    def focus_window(self, handle: int) -> None:
        """Traz uma janela existente para primeiro plano."""

    def launch_application(self, executable: Path) -> None:
        """Inicia um executável já validado pelo catálogo."""

    def is_application_running(self, process_names: tuple[str, ...]) -> bool:
        """Verifica processos usando identidades definidas pela allowlist."""

    def has_application_window(
        self,
        process_names: tuple[str, ...],
        window_classes: tuple[str, ...],
    ) -> bool:
        """Verifica janela visível ligada a um processo esperado."""

    def window_rectangle(self, handle: int) -> WindowRectangle:
        """Retorna os limites atuais de uma janela válida."""

    def move_pointer(self, x: int, y: int) -> None:
        """Move o ponteiro para uma coordenada calculada pelo serviço."""

    def pointer_position(self) -> tuple[int, int]:
        """Retorna a posição atual do ponteiro."""

    def foreground_window(self) -> int | None:
        """Retorna o handle da janela em primeiro plano."""

    def set_escape_pressed(self, pressed: bool) -> None:
        """Pressiona ou solta exclusivamente a tecla Escape."""

    def request_window_close(self, handle: int) -> None:
        """Solicita fechamento cooperativo sem encerrar o processo à força."""

    def window_exists(self, handle: int) -> bool:
        """Verifica se o handle ainda identifica uma janela válida."""
