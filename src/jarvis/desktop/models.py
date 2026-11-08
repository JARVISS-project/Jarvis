"""Modelos imutáveis usados pela camada de automação de desktop."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DesktopWindow:
    handle: int
    title: str
    process_id: int | None = None
    class_name: str | None = None


@dataclass(frozen=True, slots=True)
class DesktopApplication:
    name: str
    executable: Path
    process_names: tuple[str, ...] = ()
    window_classes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WindowRectangle:
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True, slots=True)
class PreparedDesktopWindow:
    public_target: str
    window: DesktopWindow = field(repr=False)
    rectangle: WindowRectangle | None = field(default=None, repr=False)

    def visual_identity(self) -> str:
        return "|".join(
            (
                str(self.window.handle),
                str(self.window.process_id),
                self.window.class_name or "",
            )
        )


@dataclass(frozen=True, slots=True)
class WindowCloseResult:
    window: DesktopWindow
    closed: bool
