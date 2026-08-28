from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.vision.models import CapturedFrame
from jarvis.vision.service import ScreenChange, VisionService


class FakeVisionBackend:
    def __init__(self, frames: list[CapturedFrame]) -> None:
        self.frames = frames
        self.calls = 0

    def capture_screen(self) -> CapturedFrame:
        self.calls += 1
        return self.frames.pop(0)


def frame(value: int, *, width: int = 2, height: int = 2) -> CapturedFrame:
    return CapturedFrame(width, height, "RGB", bytes([value]) * width * height * 3)


def enabled_policy() -> CapabilityPolicy:
    return CapabilityPolicy({Capability.VISION: True})


def test_observation_reports_only_change_state_and_dimensions() -> None:
    backend = FakeVisionBackend([frame(1), frame(1), frame(2)])
    service = VisionService(backend, enabled_policy())

    baseline = service.observe_change()
    unchanged = service.observe_change()
    changed = service.observe_change()

    assert baseline.status is ScreenChange.BASELINE
    assert unchanged.status is ScreenChange.UNCHANGED
    assert changed.status is ScreenChange.CHANGED
    assert (changed.width, changed.height) == (2, 2)
    assert not hasattr(changed, "pixels")
    assert not hasattr(changed, "fingerprint")


def test_disabled_vision_blocks_before_capture() -> None:
    backend = FakeVisionBackend([frame(1)])
    service = VisionService(
        backend,
        CapabilityPolicy({Capability.VISION: False}),
    )

    with pytest.raises(PermissionError, match="vision"):
        service.observe_change()

    assert backend.calls == 0


@pytest.mark.parametrize(
    "captured",
    [
        CapturedFrame(0, 1, "RGB", b""),
        CapturedFrame(2, 2, "RGBA", bytes(16)),
        CapturedFrame(2, 2, "RGB", bytes(11)),
    ],
)
def test_invalid_frames_fail_closed(captured: CapturedFrame) -> None:
    service = VisionService(
        FakeVisionBackend([captured]),
        enabled_policy(),
    )

    with pytest.raises(RuntimeError):
        service.observe_change()


def test_pixel_limit_is_enforced_without_retaining_raw_frame() -> None:
    captured = frame(7, width=3, height=2)
    service = VisionService(
        FakeVisionBackend([captured]),
        enabled_policy(),
        max_pixels=4,
    )

    with pytest.raises(RuntimeError, match="limite de pixels"):
        service.observe_change()

    assert "pixels" not in repr(captured)
    assert "070707" not in repr(service)


def test_visual_verification_is_bound_to_target_and_single_use() -> None:
    backend = FakeVisionBackend([frame(1), frame(2)])
    service = VisionService(backend, enabled_policy())
    token = service.begin_verification("desktop_focus_window:janela:Obsidian")

    result = service.finish_verification(
        token,
        "desktop_focus_window:janela:Obsidian",
    )

    assert result.status is ScreenChange.CHANGED
    assert "Obsidian" not in repr(token)
    assert "fingerprint" not in repr(token)
    with pytest.raises(ValueError, match="já consumida"):
        service.finish_verification(
            token,
            "desktop_focus_window:janela:Obsidian",
        )


def test_target_mismatch_consumes_baseline_without_post_capture() -> None:
    backend = FakeVisionBackend([frame(1), frame(2)])
    service = VisionService(backend, enabled_policy())
    token = service.begin_verification("target-a")

    with pytest.raises(ValueError, match="não corresponde"):
        service.finish_verification(token, "target-b")

    assert backend.calls == 1
    with pytest.raises(ValueError, match="já consumida"):
        service.finish_verification(token, "target-a")


def test_expired_or_cancelled_baseline_cannot_be_reused() -> None:
    now = [0.0]
    backend = FakeVisionBackend([frame(1), frame(2)])
    service = VisionService(
        backend,
        enabled_policy(),
        verification_max_age_seconds=1.0,
        monotonic=lambda: now[0],
    )
    expired = service.begin_verification("target")
    now[0] = 2.0
    with pytest.raises(TimeoutError, match="expirada"):
        service.finish_verification(expired, "target")

    now[0] = 3.0
    cancelled = service.begin_verification("target")
    service.cancel_verification(cancelled)
    with pytest.raises(ValueError, match="já consumida"):
        service.finish_verification(cancelled, "target")


def test_concurrent_visual_verifications_keep_tokens_isolated() -> None:
    backend = FakeVisionBackend([frame(1), frame(2), frame(3), frame(4)])
    service = VisionService(backend, enabled_policy())

    def verify(target: str) -> ScreenChange:
        token = service.begin_verification(target)
        return service.finish_verification(token, target).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(verify, ["target-a", "target-b"]))

    assert results == [ScreenChange.CHANGED, ScreenChange.CHANGED]
    assert backend.calls == 4
