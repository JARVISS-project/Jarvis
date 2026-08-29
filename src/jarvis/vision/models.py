"""Modelos internos da fundação Vision."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    width: int
    height: int
    mode: str
    pixels: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class CaptureRegion:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class TextResult:
    text: str
    confidence: float
    region: CaptureRegion
    language: str | None = None


@dataclass(frozen=True, slots=True)
class UIElement:
    name: str | None
    control_type: str
    bounding_rect: CaptureRegion
    children: tuple["UIElement", ...] = field(default_factory=tuple)
    properties: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class UITreeResult:
    root: UIElement
    timestamp: float
