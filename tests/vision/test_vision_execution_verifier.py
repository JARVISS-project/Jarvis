from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.vision.execution_verifier import VisionToolExecutionVerifier
from jarvis.vision.models import CapturedFrame
from jarvis.vision.service import VisionService


class FakeBackend:
    def __init__(self) -> None:
        self.frames = [
            CapturedFrame(1, 1, "RGB", b"aaa"),
            CapturedFrame(1, 1, "RGB", b"bbb"),
        ]
        self.calls = 0

    def capture_screen(self) -> CapturedFrame:
        self.calls += 1
        return self.frames.pop(0)


def test_verifier_captures_only_supported_desktop_tools() -> None:
    backend = FakeBackend()
    service = VisionService(
        backend,
        CapabilityPolicy({Capability.VISION: True}),
    )
    verifier = VisionToolExecutionVerifier(service)

    assert verifier.before("read_file", "arquivo:test.txt") is None
    token = verifier.before("desktop_focus_window", "janela:Obsidian")
    signal = verifier.after(
        token,
        "desktop_focus_window",
        "janela:Obsidian",
    )

    assert signal == "changed"
    assert backend.calls == 2
