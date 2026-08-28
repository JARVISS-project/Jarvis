"""Captura local read-only usando Pillow ImageGrab."""

from jarvis.vision.models import CapturedFrame


class PillowVisionBackend:
    def __init__(self, *, max_pixels: int = 33_177_600) -> None:
        if max_pixels <= 0:
            raise ValueError("max_pixels deve ser positivo.")
        self.max_pixels = max_pixels

    def capture_screen(self) -> CapturedFrame:
        try:
            from PIL import ImageGrab
        except ImportError as error:
            raise RuntimeError(
                "Vision requer a dependência opcional Pillow."
            ) from error

        image = ImageGrab.grab(all_screens=True)
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
