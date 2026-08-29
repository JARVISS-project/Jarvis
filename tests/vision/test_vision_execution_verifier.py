from jarvis.desktop.models import (
    DesktopWindow,
    PreparedDesktopWindow,
    WindowRectangle,
)
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.vision.execution_verifier import VisionToolExecutionVerifier
from jarvis.vision.models import CaptureRegion, CapturedFrame
from jarvis.vision.service import VisionService


class FakeBackend:
    def __init__(self) -> None:
        self.frames = [
            CapturedFrame(1, 1, "RGB", b"aaa"),
            CapturedFrame(1, 1, "RGB", b"bbb"),
        ]
        self.calls = 0
        self.regions: list[CaptureRegion] = []

    def capture_screen(self) -> CapturedFrame:
        self.calls += 1
        return self.frames.pop(0)

    def capture_region(self, region: CaptureRegion) -> CapturedFrame:
        self.calls += 1
        self.regions.append(region)
        return self.frames.pop(0)


def test_verifier_captures_only_supported_desktop_tools() -> None:
    backend = FakeBackend()
    service = VisionService(
        backend,
        CapabilityPolicy({Capability.VISION: True}),
    )
    verifier = VisionToolExecutionVerifier(service)

    assert verifier.before("read_file", "arquivo:test.txt") is None
    prepared = PreparedDesktopWindow(
        "janela:Obsidian",
        DesktopWindow(7, "Obsidian", 70, "TestWindow"),
        WindowRectangle(10, 20, 11, 21),
    )
    token = verifier.before(
        "desktop_focus_window",
        "janela:Obsidian",
        prepared,
    )
    signal = verifier.after(
        token,
        "desktop_focus_window",
        "janela:Obsidian",
        prepared,
    )

    assert signal == "changed"
    assert backend.calls == 2
    assert backend.regions == [CaptureRegion(10, 20, 11, 21)] * 2


def test_verifier_does_not_fallback_to_full_screen_without_prepared_window() -> None:
    backend = FakeBackend()
    verifier = VisionToolExecutionVerifier(
        VisionService(
            backend,
            CapabilityPolicy({Capability.VISION: True}),
        )
    )

    assert verifier.before("desktop_open_application", "aplicativo:notepad") is None
    assert verifier.before("desktop_focus_window", "janela:Obsidian") is None
    assert backend.calls == 0
