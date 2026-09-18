"""Serviço de domínio para automação segura de navegador."""

import threading
import time
import re
import unicodedata
from typing import Callable, Any

from jarvis.browser.backend import BrowserBackend
from jarvis.browser.models import (
    AuthorizedFillOperation,
    AuthorizedNavigateOperation,
    AuthorizedTypeTextOperation,
    BrowserActionResult,
    BrowserElement,
    BrowserExtractResult,
    BrowserNavigationResult,
    BrowserPageSnapshot,
    BrowserTab,
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
        if url == "about:blank":
            return
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.fragment)
        ):
            raise PermissionError("URL de navegação inválida ou não permitida.")
        domain = parsed.hostname.rstrip(".").lower()

        def matches(rule: str) -> bool:
            normalized = rule.rstrip(".").lower()
            return domain == normalized or domain.endswith(f".{normalized}")

        # Allowlist tem prioridade: se existe, só permite domínios nela
        if self.allowed_domains:
            if not any(matches(item) for item in self.allowed_domains):
                raise PermissionError(f"Domínio não permitido: {domain}")
            return  # Se passou na allowlist, não verifica blocklist

        # Se não há allowlist, verifica blocklist
        if self.blocked_domains and any(
            matches(item) for item in self.blocked_domains
        ):
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

    async def navigate(
        self,
        operation: AuthorizedNavigateOperation,
    ) -> BrowserNavigationResult:
        """Navega para uma URL."""
        if not isinstance(operation, AuthorizedNavigateOperation):
            raise PermissionError("Payload NAVIGATE autorizado ausente.")
        operation.require_executing()
        self.capability_policy.require(Capability.BROWSER)
        self._ensure_launched()
        self._check_domain(operation.url)
        return await self.backend.navigate(operation)

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

    async def type_text(
        self,
        operation: AuthorizedTypeTextOperation,
    ) -> BrowserActionResult:
        """Digita texto em um elemento."""
        if not isinstance(operation, AuthorizedTypeTextOperation):
            raise PermissionError("Payload TYPE_TEXT autorizado ausente.")
        operation.require_executing()
        self.capability_policy.require(Capability.BROWSER)
        self._ensure_launched()
        await self._require_bound_page(operation.tab_id, operation.page_url)
        return await self.backend.type_text(operation)

    async def fill(
        self,
        operation: AuthorizedFillOperation,
    ) -> BrowserActionResult:
        """Preenche um campo de formulário."""
        if not isinstance(operation, AuthorizedFillOperation):
            raise PermissionError("Payload FILL autorizado ausente.")
        operation.require_executing()
        self.capability_policy.require(Capability.BROWSER)
        self._ensure_launched()
        await self._require_bound_page(operation.tab_id, operation.page_url)
        return await self.backend.fill(operation)

    async def _require_bound_page(self, tab_id: str, page_url: str) -> None:
        tabs = await self.backend.list_tabs()
        tab = next((item for item in tabs if item.id == tab_id), None)
        if tab is None or tab.url != page_url:
            raise PermissionError("A rota autorizada da aba não está mais ativa.")

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
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.list_tabs())
            except RuntimeError as error:
                if "não iniciado" in str(error):
                    return ()
                raise
            finally:
                loop.close()
        else:
            # Se já está rodando, criamos uma task e aguardamos
            result_container: list[tuple[BrowserTab, ...]] = []
            exception_container: list[Exception] = []

            def run_in_new_loop() -> None:
                new_loop = asyncio.new_event_loop()
                try:
                    result_container.append(
                        new_loop.run_until_complete(self.list_tabs())
                    )
                except Exception as error:
                    exception_container.append(error)
                finally:
                    new_loop.close()

            thread = threading.Thread(target=run_in_new_loop)
            thread.start()
            thread.join()

            if exception_container:
                raise exception_container[0]
            return result_container[0] if result_container else ()
