"""Modelos imutáveis para automação de navegador."""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class BrowserTab:
    url: str
    title: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    is_active: bool = False


@dataclass(frozen=True, slots=True)
class BrowserElement:
    selector: str
    tag_name: str
    text: str | None = None
    attributes: dict[str, str] = field(default_factory=dict, repr=False)
    is_visible: bool = True
    is_enabled: bool = True
    bounding_box: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class BrowserPageSnapshot:
    url: str
    title: str
    tabs: tuple[BrowserTab, ...]
    selected_text: str | None = None
    viewport: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class BrowserNavigationResult:
    url: str
    title: str
    success: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    success: bool
    message: str
    element: BrowserElement | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserExtractResult:
    success: bool
    data: dict[str, Any] | list[Any] | str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedBrowserTarget:
    """Identidade preparada de um alvo no navegador."""
    public_target: str
    tab_id: str
    selector: str | None = field(default=None, repr=False)
    url: str | None = field(default=None, repr=False)

    def binding_identity(self) -> str:
        parts = [self.tab_id]
        if self.selector:
            parts.append(self.selector)
        if self.url:
            parts.append(self.url)
        return "|".join(parts)