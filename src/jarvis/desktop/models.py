"""Modelos imutáveis usados pela camada de automação de desktop."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DesktopWindow:
    handle: int
    title: str
    process_id: int | None = None


@dataclass(frozen=True, slots=True)
class DesktopApplication:
    name: str
    executable: Path
    process_names: tuple[str, ...] = ()
