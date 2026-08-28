"""Contrato do backend local de captura de tela."""

from typing import Protocol

from jarvis.vision.models import CapturedFrame


class VisionBackend(Protocol):
    def capture_screen(self) -> CapturedFrame:
        """Captura a tela em memória sem persistir ou publicar o frame."""
