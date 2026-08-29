"""Backend composto que combina Pillow (captura/OCR) e Windows UI Automation (UI tree)."""

from jarvis.vision.backend import VisionBackend
from jarvis.vision.models import CaptureRegion, CapturedFrame, TextResult, UITreeResult
from jarvis.vision.pillow_backend import PillowVisionBackend
from jarvis.vision.windows_uia_backend import WindowsUIAutomationBackend


class CompositeVisionBackend(VisionBackend):
    def __init__(self, *, max_pixels: int = 33_177_600) -> None:
        self._pillow = PillowVisionBackend(max_pixels=max_pixels)
        self._uia = WindowsUIAutomationBackend()

    def capture_screen(self) -> CapturedFrame:
        return self._pillow.capture_screen()

    def capture_region(self, region: CaptureRegion) -> CapturedFrame:
        return self._pillow.capture_region(region)

    def extract_text(self, region: CaptureRegion, language: str | None = None) -> TextResult:
        return self._pillow.extract_text(region, language)

    def get_ui_tree(self, region: CaptureRegion | None = None) -> UITreeResult:
        return self._uia.get_ui_tree(region)