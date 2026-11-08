import sys
from types import SimpleNamespace

import pytest

from jarvis.vision.models import CaptureRegion
from jarvis.vision.pillow_backend import PillowVisionBackend


class FakeImage:
    def __init__(self, size: tuple[int, int], pixels: bytes) -> None:
        self.size = size
        self.pixels = pixels
        self.closed = False
        self.converted: FakeImage | None = None

    def convert(self, mode: str) -> "FakeImage":
        assert mode == "RGB"
        self.converted = FakeImage(self.size, self.pixels)
        return self.converted

    def tobytes(self) -> bytes:
        return self.pixels

    def close(self) -> None:
        self.closed = True


def test_region_capture_uses_exact_multimonitor_bounds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []
    image = FakeImage((200, 100), bytes(200 * 100 * 3))

    def grab(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return image

    monkeypatch.setitem(
        sys.modules,
        "PIL",
        SimpleNamespace(ImageGrab=SimpleNamespace(grab=grab)),
    )

    captured = PillowVisionBackend().capture_region(
        CaptureRegion(-200, 20, 0, 120)
    )

    assert calls == [
        {"bbox": (-200, 20, 0, 120), "all_screens": True}
    ]
    assert (captured.width, captured.height, captured.mode) == (200, 100, "RGB")
    assert image.closed
    assert image.converted is not None and image.converted.closed


def test_invalid_or_oversized_region_fails_before_image_grab(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls = 0

    def grab(**_kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        raise AssertionError("captura não deveria ocorrer")

    monkeypatch.setitem(
        sys.modules,
        "PIL",
        SimpleNamespace(ImageGrab=SimpleNamespace(grab=grab)),
    )
    backend = PillowVisionBackend(max_pixels=10)

    with pytest.raises(RuntimeError, match="dimensões inválidas"):
        backend.capture_region(CaptureRegion(0, 0, 0, 1))
    with pytest.raises(RuntimeError, match="limite de pixels"):
        backend.capture_region(CaptureRegion(0, 0, 11, 1))

    assert calls == 0


def test_capture_closes_source_image_when_conversion_fails(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    image = FakeImage((1, 1), bytes(3))

    def fail_convert(_mode: str) -> FakeImage:
        raise RuntimeError("conversion failed")

    image.convert = fail_convert  # type: ignore[method-assign]
    monkeypatch.setitem(
        sys.modules,
        "PIL",
        SimpleNamespace(
            ImageGrab=SimpleNamespace(grab=lambda **_kwargs: image)
        ),
    )

    with pytest.raises(RuntimeError, match="conversion failed"):
        PillowVisionBackend().capture_screen()

    assert image.closed
