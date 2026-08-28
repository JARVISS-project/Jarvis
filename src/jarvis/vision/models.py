"""Modelos internos da fundação Vision."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    width: int
    height: int
    mode: str
    pixels: bytes = field(repr=False)
