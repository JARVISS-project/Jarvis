"""Serviço de domínio para automação segura de navegador."""

import threading
import time
import re
import unicodedata
from typing import Callable, Any

from jarvis.browser.backend import BrowserBackend
from jarvis.browser.models import (
    BrowserActionResult,
    BrowserElement,
    BrowserExtractResult,
    BrowserNavigationResult,
    BrowserPageSnapshot,
    BrowserTab,
    PreparedBrowserTarget,
)
from jarvis.security.capabilities import Capability, CapabilityPolicy


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


class BrowserService:
    """Serviço de automação de navegador com validações de segurança."""

    def __init__(
        self,
        backend: BrowserBackend,
        capability_policy: CapabilityPolicy,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        max_tabs: int = 10,
        navigation_timeout: float = 30.0,
        action_timeout: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        from jarvis.security.capabilities import CapabilityPolicy
        self.backend = backend
        self.capability_policy = capability_policy
        self.allowed_domains = [d.lower() for d in (allowed_domains or [])]
        self.blocked_domains = [d.lower() for d in (blocked_domains or [])]
        self.max_tabs = max_tabs
        self.navigation_timeout = navigation_timeout
        self.action_timeout = action_timeout
        self.monotonic = monotonic
        self._launched = False

    def _check_domain(self, url: str) -> None:
        """Valida se o domínio é permitido."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Allowlist tem prioridade: se existe, só permite domínios nela
        if self.allowed_domains:
            if not any(d in domain for d in self.allowed_domains):
                raise PermissionError(f"Domínio não permitido: {domain}")
            return  # Se passou na allowlist, não verifica blocklist

        # Se não há allowlist, verifica blocklist
        if self.blocked_domains and any(d in domain for d in self.blocked_domains):
            raise PermissionError(f"Domínio bloqueado: {domain}")

    def _ensure_launched(self) -> None:
        """Garante que o navegador foi iniciado."""
        if not self._launched:
            raise RuntimeError("Navegador não iniciado. Chame launch() primeiro.")

    async def launch(self, headless: bool = False) -> None:
        """Inicia o navegador."""
        await self.backend.launch(headless=headless)
        self._launched = True

    async def close(self) -> None:
        """Fecha o navegador."""
        await self.backend.close()
        self._launched = False

    async def list_tabs(self) -> tuple[BrowserTab, ...]:
        """Lista todas as abas abertas."""
        self.capability_policy.require(Capability.BROWSER)
        self._ensure_launched()
        return await self.backend.list_tabs()

    async def new_tab(self, url: str = "about:blank") -> BrowserTab:
        """Abre uma nova aba."""
        self._ensure_launched()
        tabs = await self.backend.list_tabs()
        if len(tabs) >= self.max_tabs:
            raise RuntimeError(f"Limite de {self.max_tabs} abas atingido.")
        self._check_domain(url)
        return await self.backend.new_tab(url)

    async def close_tab(self, tab_id: str) -> bool:
        """Fecha uma aba."""
        self._ensure_launched()
        return await self.backend.close_tab(tab_id)

    async def switch_tab(self, tab_id: str) -> BrowserTab:
        """Muda para uma aba específica."""
        self._ensure_launched()
        return await self.backend.switch_tab(tab_id)

    async def navigate(self, tab_id: str, url: str) -> BrowserNavigationResult:
        """Navega para uma URL."""
        self._ensure_launched()
        self._check_domain(url)
        return await self.backend.navigate(tab_id, url)

    async def go_back(self, tab_id: str) -> BrowserNavigationResult:
        """Volta no histórico."""
        self._ensure_launched()
        return await self.backend.go_back(tab_id)

    async def go_forward(self, tab_id: str) -> BrowserNavigationResult:
        """Avança no histórico."""
        self._ensure_launched()
        return await self.backend.go_forward(tab_id)

    async def reload(self, tab_id: str) -> BrowserNavigationResult:
        """Recarrega a página."""
        self._ensure_launched()
        return await self.backend.reload(tab_id)

    async def click(self, tab_id: str, selector: str) -> BrowserActionResult:
        """Clica em um elemento."""
        self._ensure_launched()
        return await self.backend.click(tab_id, selector)

    async def type_text(self, tab_id: str, selector: str, text: str) -> BrowserActionResult:
        """Digita texto em um elemento."""
        self._ensure_launched()
        return await self.backend.type_text(tab_id, selector, text)

    async def fill(self, tab_id: str, selector: str, text: str) -> BrowserActionResult:
        """Preenche um campo de formulário."""
        self._ensure_launched()
        return await self.backend.fill(tab_id, selector, text)

    async def press_key(self, tab_id: str, key: str) -> BrowserActionResult:
        """Pressiona uma tecla."""
        self._ensure_launched()
        # Valida teclas permitidas
        allowed_keys = {
            "Enter", "Escape", "Tab", "Backspace", "Delete",
            "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
            "Home", "End", "PageUp", "PageDown",
            "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
        }
        if key not in allowed_keys:
            raise ValueError(f"Tecla não permitida: {key}")
        return await self.backend.press_key(tab_id, key)

    async def extract_text(self, tab_id: str, selector: str) -> BrowserExtractResult:
        """Extrai texto de um elemento."""
        self._ensure_launched()
        return await self.backend.extract_text(tab_id, selector)

    async def extract_attribute(
        self, tab_id: str, selector: str, attribute: str
    ) -> BrowserExtractResult:
        """Extrai atributo de um elemento."""
        self._ensure_launched()
        return await self.backend.extract_attribute(tab_id, selector, attribute)

    async def extract_multiple(
        self, tab_id: str, selector: str, attributes: list[str]
    ) -> BrowserExtractResult:
        """Extrai múltiplos elementos."""
        self._ensure_launched()
        return await self.backend.extract_multiple(tab_id, selector, attributes)

    async def wait_for_selector(
        self, tab_id: str, selector: str, timeout: float | None = None
    ) -> BrowserActionResult:
        """Aguarda um elemento aparecer."""
        self._ensure_launched()
        return await self.backend.wait_for_selector(tab_id, selector, timeout or self.action_timeout)

    async def get_page_snapshot(self, tab_id: str) -> BrowserPageSnapshot:
        """Obtém snapshot da página atual."""
        self._ensure_launched()
        return await self.backend.get_page_snapshot(tab_id)

    async def execute_script(self, tab_id: str, script: str) -> BrowserExtractResult:
        """Executa JavaScript na página."""
        self._ensure_launched()
        # Lista de scripts permitidos (allowlist)
        allowed_patterns = [
            r"^document\.querySelector",
            r"^document\.getElementById",
            r"^document\.title",
            r"^window\.location",
            r"^window\.innerWidth",
            r"^window\.innerHeight",
        ]
        if not any(re.match(p, script.strip()) for p in allowed_patterns):
            raise PermissionError("Script não permitido. Use padrões da allowlist.")
        return await self.backend.execute_script(tab_id, script)

    async def screenshot(self, tab_id: str, full_page: bool = False) -> bytes:
        """Captura screenshot da página."""
        self._ensure_launched()
        return await self.backend.screenshot(tab_id, full_page)

    def _check_key(self, key: str) -> None:
        """Valida se a tecla está na allowlist."""
        allowed_keys = {
            "Enter", "Escape", "Tab", "Backspace", "Delete",
            "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
            "Home", "End", "PageUp", "PageDown",
            "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
        }
        if key not in allowed_keys:
            raise ValueError(f"Tecla não permitida: {key}")

    def _check_script(self, script: str) -> None:
        """Valida se o script está na allowlist."""
        allowed_patterns = [
            r"^document\.querySelector",
            r"^document\.getElementById",
            r"^document\.title",
            r"^window\.location",
            r"^window\.innerWidth",
            r"^window\.innerHeight",
        ]
        if not any(re.match(p, script.strip()) for p in allowed_patterns):
            raise PermissionError("Script não permitido. Use padrões da allowlist.")

    # Métodos síncronos para preflight (usados pelas Tools)
    def list_tabs_sync(self, tab_id: str | None = None) -> tuple[BrowserTab, ...]:
        """Versão síncrona de list_tabs para preflight. Retorna tupla vazia se navegador não iniciado."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Se já está rodando, criamos uma task e aguardamos
                import threading
                result_container = []
                exception_container = []
                
                def run_in_new_loop():
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        result = new_loop.run_until_complete(self.list_tabs())
                        result_container.append(result)
                    except Exception as e:
                        exception_container.append(e)
                    finally:
                        new_loop.close()
                
                thread = threading.Thread(target=run_in_new_loop)
                thread.start()
                thread.join()
                
                if exception_container:
                    raise exception_container[0]
                return result_container[0] if result_container else ()
            if loop.is_closed():
                raise RuntimeError("Event loop is closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.list_tabs())
            except RuntimeError as e:
                if "não iniciado" in str(e):
                    return ()
                raise