"""Contrato do backend local de captura de tela."""

from typing import Protocol

from jarvis.vision.models import CaptureRegion, CapturedFrame, TextResult, UITreeResult


class VisionBackend(Protocol):
    def capture_screen(self) -> CapturedFrame:
        """Captura a tela em memória sem persistir ou publicar o frame."""

    def capture_region(self, region: CaptureRegion) -> CapturedFrame:
        """Captura somente uma região validada da tela em memória."""

    def extract_text(self, region: CaptureRegion, language: str | None = None) -> TextResult:
        """Extrai texto de uma região usando OCR. Não persiste o frame."""

    def get_ui_tree(self, region: CaptureRegion | None = None) -> UITreeResult:
        """Captura a árvore de acessibilidade/UI da região ou tela inteira."""
