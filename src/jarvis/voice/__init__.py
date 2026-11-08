from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jarvis.voice.capture_result import VoiceCaptureResult, VoiceCaptureStatus
from jarvis.voice.state import VoicePipelineState

if TYPE_CHECKING:
    from jarvis.voice.pipeline import VoicePipeline

__all__ = [
    "VoiceCaptureResult",
    "VoiceCaptureStatus",
    "VoicePipeline",
    "VoicePipelineState",
]


def __getattr__(name: str) -> Any:
    if name == "VoicePipeline":
        from jarvis.voice.pipeline import VoicePipeline

        globals()[name] = VoicePipeline
        return VoicePipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
