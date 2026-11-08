"""Captura local read-only usando Pillow ImageGrab e OCR opcional."""

import sys
from types import SimpleNamespace

from jarvis.vision.models import CaptureRegion, CapturedFrame, TextResult, UITreeResult, UIElement


class PillowVisionBackend:
    def __init__(self, *, max_pixels: int = 33_177_600) -> None:
        if max_pixels <= 0:
            raise ValueError("max_pixels deve ser positivo.")
        self.max_pixels = max_pixels
        self._tesseract_available = self._check_tesseract()

    def _check_tesseract(self) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def capture_screen(self) -> CapturedFrame:
        return self._capture(None)

    def capture_region(self, region: CaptureRegion) -> CapturedFrame:
        if region.width <= 0 or region.height <= 0:
            raise RuntimeError("A região de captura possui dimensões inválidas.")
        if region.width * region.height > self.max_pixels:
            raise RuntimeError("A região de captura excede o limite de pixels.")
        return self._capture(
            (region.left, region.top, region.right, region.bottom)
        )

    def extract_text(
        self, region: CaptureRegion, language: str | None = None
    ) -> TextResult:
        if not self._tesseract_available:
            raise RuntimeError(
                "OCR requer pytesseract e tesseract-ocr instalados."
            )
        if region.width <= 0 or region.height <= 0:
            raise RuntimeError("A região de captura possui dimensões inválidas.")
        if region.width * region.height > self.max_pixels:
            raise RuntimeError("A região de captura excede o limite de pixels.")

        try:
            import pytesseract
            from PIL import ImageGrab
        except ImportError as error:
            raise RuntimeError(
                "OCR requer a dependência opcional pytesseract."
            ) from error

        bbox = (region.left, region.top, region.right, region.bottom)
        image = ImageGrab.grab(bbox=bbox, all_screens=True)
        try:
            if image.mode != "RGB":
                image = image.convert("RGB")
            text = pytesseract.image_to_string(
                image, lang=language or "por+eng"
            ).strip()
            data = pytesseract.image_to_data(
                image, lang=language or "por+eng", output_type=pytesseract.Output.DICT
            )
            confidences = [
                float(c) for c in data.get("conf", []) if c != "-1"
            ]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            return TextResult(
                text=text,
                confidence=avg_confidence / 100.0,
                region=region,
                language=language,
            )
        finally:
            image.close()

    def get_ui_tree(
        self, region: CaptureRegion | None = None
    ) -> UITreeResult:
        raise NotImplementedError(
            "UI tree capture requer backend Windows UI Automation."
        )

    def _capture(
        self,
        bbox: tuple[int, int, int, int] | None,
    ) -> CapturedFrame:
        try:
            from PIL import ImageGrab
        except ImportError as error:
            raise RuntimeError(
                "Vision requer a dependência opcional Pillow."
            ) from error

        image = ImageGrab.grab(bbox=bbox, all_screens=True)
        rgb_image = None
        try:
            width, height = image.size
            if width <= 0 or height <= 0:
                raise RuntimeError("A captura de tela possui dimensões inválidas.")
            if width * height > self.max_pixels:
                raise RuntimeError("A captura de tela excede o limite de pixels.")
            rgb_image = image.convert("RGB")
            return CapturedFrame(
                width=int(width),
                height=int(height),
                mode="RGB",
                pixels=rgb_image.tobytes(),
            )
        finally:
            if rgb_image is not None:
                rgb_image.close()
            image.close()
