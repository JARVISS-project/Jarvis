"""Backend de navegador usando Playwright."""

from __future__ import annotations

from typing import Protocol

from jarvis.browser.models import (
    BrowserActionResult,
    BrowserElement,
    BrowserExtractResult,
    BrowserNavigationResult,
    BrowserPageSnapshot,
    BrowserTab,
    PreparedBrowserTarget,
)


class BrowserBackend(Protocol):
    """Contrato do backend de navegador."""

    async def launch(self, headless: bool = False) -> None:
        """Inicia o navegador."""

    async def close(self) -> None:
        """Fecha o navegador."""

    async def new_tab(self, url: str = "about:blank") -> BrowserTab:
        """Abre uma nova aba."""

    async def close_tab(self, tab_id: str) -> bool:
        """Fecha uma aba."""

    async def list_tabs(self) -> tuple[BrowserTab, ...]:
        """Lista todas as abas."""

    async def switch_tab(self, tab_id: str) -> BrowserTab:
        """Muda para uma aba específica."""

    async def navigate(self, tab_id: str, url: str) -> BrowserNavigationResult:
        """Navega para uma URL."""

    async def go_back(self, tab_id: str) -> BrowserNavigationResult:
        """Volta no histórico."""

    async def go_forward(self, tab_id: str) -> BrowserNavigationResult:
        """Avança no histórico."""

    async def reload(self, tab_id: str) -> BrowserNavigationResult:
        """Recarrega a página."""

    async def click(self, tab_id: str, selector: str) -> BrowserActionResult:
        """Clica em um elemento."""

    async def type_text(self, tab_id: str, selector: str, text: str) -> BrowserActionResult:
        """Digita texto em um elemento."""

    async def fill(self, tab_id: str, selector: str, text: str) -> BrowserActionResult:
        """Preenche um campo de formulário."""

    async def press_key(self, tab_id: str, key: str) -> BrowserActionResult:
        """Pressiona uma tecla."""

    async def extract_text(self, tab_id: str, selector: str) -> BrowserExtractResult:
        """Extrai texto de um elemento."""

    async def extract_attribute(
        self, tab_id: str, selector: str, attribute: str
    ) -> BrowserExtractResult:
        """Extrai atributo de um elemento."""

    async def extract_multiple(
        self, tab_id: str, selector: str, attributes: list[str]
    ) -> BrowserExtractResult:
        """Extrai múltiplos elementos."""

    async def wait_for_selector(
        self, tab_id: str, selector: str, timeout: float = 30.0
    ) -> BrowserActionResult:
        """Aguarda um elemento aparecer."""

    async def get_page_snapshot(self, tab_id: str) -> BrowserPageSnapshot:
        """Obtém snapshot da página atual."""

    async def execute_script(self, tab_id: str, script: str) -> BrowserExtractResult:
        """Executa JavaScript na página."""

    async def screenshot(self, tab_id: str, full_page: bool = False) -> bytes:
        """Captura screenshot da página."""