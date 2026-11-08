"""Implementação do backend de navegador usando Playwright."""

import asyncio
from typing import Any
from urllib.parse import urlsplit

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


class PlaywrightBackend(BrowserBackend):
    """Backend de navegador usando Playwright."""

    def __init__(
        self,
        browser_type: str = "chromium",
        headless: bool = False,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        default_timeout: float = 30.0,
    ) -> None:
        self.browser_type = browser_type
        self.headless = headless
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.default_timeout = default_timeout
        self._playwright = None
        self._browser = None
        self._context = None
        self._tabs: dict[str, Any] = {}  # tab_id -> page
        self._navigation_guards: dict[str, Any] = {}
        self._tab_counter = 0

    async def launch(self, headless: bool = False) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright não está instalado. Instale com: pip install playwright && playwright install"
            ) from error

        self._playwright = await async_playwright().start()
        browser_launcher = getattr(self._playwright, self.browser_type)
        self._browser = await browser_launcher.launch(headless=headless)
        self._context = await self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            service_workers="block",
        )

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._tabs.clear()
        self._navigation_guards.clear()

    def _get_page(self, tab_id: str) -> Any:
        page = self._tabs.get(tab_id)
        if page is None:
            raise ValueError(f"Aba não encontrada: {tab_id}")
        return page

    async def new_tab(self, url: str = "about:blank") -> BrowserTab:
        if self._context is None:
            raise RuntimeError("Navegador não iniciado. Chame launch() primeiro.")

        page = await self._context.new_page()
        page.set_default_timeout(self.default_timeout * 1000)

        self._tab_counter += 1
        tab_id = f"tab_{self._tab_counter}"
        self._tabs[tab_id] = page

        if url != "about:blank":
            await page.goto(url, wait_until="domcontentloaded")

        title = await page.title()
        tab = BrowserTab(id=tab_id, url=url, title=title, is_active=True)
        return tab

    async def close_tab(self, tab_id: str) -> bool:
        page = self._tabs.pop(tab_id, None)
        self._navigation_guards.pop(tab_id, None)
        if page is None:
            return False
        await page.close()
        return True

    async def list_tabs(self) -> tuple[BrowserTab, ...]:
        tabs = []
        for tab_id, page in self._tabs.items():
            try:
                url = page.url
                title = await page.title()
                tabs.append(BrowserTab(id=tab_id, url=url, title=title))
            except Exception:
                tabs.append(BrowserTab(id=tab_id, url="", title="", is_active=False))
        return tuple(tabs)

    async def switch_tab(self, tab_id: str) -> BrowserTab:
        page = self._get_page(tab_id)
        await page.bring_to_front()
        url = page.url
        title = await page.title()
        return BrowserTab(id=tab_id, url=url, title=title, is_active=True)

    async def navigate(
        self,
        operation: AuthorizedNavigateOperation,
    ) -> BrowserNavigationResult:
        if not isinstance(operation, AuthorizedNavigateOperation):
            raise PermissionError("Payload NAVIGATE autorizado ausente.")
        operation.require_executing()
        page = self._get_page(operation.tab_id)

        async def enforce_authorized_route(route, request) -> None:  # type: ignore[no-untyped-def]
            is_main_navigation = (
                request.is_navigation_request()
                and request.frame == page.main_frame
            )
            if is_main_navigation and not self._same_navigation_target(
                operation.url,
                request.url,
                include_fragment=False,
            ):
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        try:
            operation.claim_effect()
            previous_guard = self._navigation_guards.get(operation.tab_id)
            if previous_guard is not None:
                await page.unroute("**/*", previous_guard)
            await page.route("**/*", enforce_authorized_route)
            self._navigation_guards[operation.tab_id] = (
                enforce_authorized_route
            )
            await page.goto(operation.url, wait_until="domcontentloaded")
            final_url = page.url
            if not self._same_navigation_target(
                operation.url,
                final_url,
                include_fragment=True,
            ):
                return BrowserNavigationResult(
                    url=final_url,
                    title="",
                    success=False,
                    error="A navegação terminou em uma rota não autorizada.",
                )
            title = await page.title()
            return BrowserNavigationResult(
                url=final_url,
                title=title,
                success=True,
            )
        except Exception as e:
            return BrowserNavigationResult(
                url=operation.url,
                title="",
                success=False,
                error=str(e),
            )

    @staticmethod
    def _same_navigation_target(
        expected: str,
        actual: str,
        *,
        include_fragment: bool = True,
    ) -> bool:
        def normalized(value: str) -> tuple[object, ...]:
            parsed = urlsplit(value)
            default_port = 443 if parsed.scheme.lower() == "https" else 80
            return (
                parsed.scheme.lower(),
                parsed.username,
                parsed.password,
                (parsed.hostname or "").rstrip(".").lower(),
                parsed.port or default_port,
                parsed.path or "/",
                parsed.query,
                parsed.fragment if include_fragment else "",
            )

        try:
            return normalized(expected) == normalized(actual)
        except ValueError:
            return False

    async def go_back(self, tab_id: str) -> BrowserNavigationResult:
        page = self._get_page(tab_id)
        try:
            await page.go_back(wait_until="domcontentloaded")
            url = page.url
            title = await page.title()
            return BrowserNavigationResult(url=url, title=title, success=True)
        except Exception as e:
            return BrowserNavigationResult(url="", title="", success=False, error=str(e))

    async def go_forward(self, tab_id: str) -> BrowserNavigationResult:
        page = self._get_page(tab_id)
        try:
            await page.go_forward(wait_until="domcontentloaded")
            url = page.url
            title = await page.title()
            return BrowserNavigationResult(url=url, title=title, success=True)
        except Exception as e:
            return BrowserNavigationResult(url="", title="", success=False, error=str(e))

    async def reload(self, tab_id: str) -> BrowserNavigationResult:
        page = self._get_page(tab_id)
        try:
            await page.reload(wait_until="domcontentloaded")
            url = page.url
            title = await page.title()
            return BrowserNavigationResult(url=url, title=title, success=True)
        except Exception as e:
            return BrowserNavigationResult(url="", title="", success=False, error=str(e))

    async def _find_element(self, tab_id: str, selector: str) -> Any:
        page = self._get_page(tab_id)
        element = await page.query_selector(selector)
        if element is None:
            raise ValueError(f"Elemento não encontrado: {selector}")
        return element

    async def click(self, tab_id: str, selector: str) -> BrowserActionResult:
        try:
            element = await self._find_element(tab_id, selector)
            await element.click()
            return BrowserActionResult(success=True, message="Clique realizado", element=None)
        except Exception as e:
            return BrowserActionResult(success=False, message="", error=str(e))

    async def type_text(
        self,
        operation: AuthorizedTypeTextOperation,
    ) -> BrowserActionResult:
        if not isinstance(operation, AuthorizedTypeTextOperation):
            raise PermissionError("Payload TYPE_TEXT autorizado ausente.")
        operation.require_executing()
        try:
            element = await self._find_authorized_element(
                operation.tab_id,
                operation.page_url,
                operation.selector,
            )
            operation.claim_effect()
            await element.type(operation.text)
            return BrowserActionResult(success=True, message="Texto digitado", element=None)
        except Exception as e:
            return BrowserActionResult(success=False, message="", error=str(e))

    async def fill(
        self,
        operation: AuthorizedFillOperation,
    ) -> BrowserActionResult:
        if not isinstance(operation, AuthorizedFillOperation):
            raise PermissionError("Payload FILL autorizado ausente.")
        operation.require_executing()
        try:
            element = await self._find_authorized_element(
                operation.tab_id,
                operation.page_url,
                operation.selector,
            )
            operation.claim_effect()
            await element.fill(operation.text)
            return BrowserActionResult(success=True, message="Campo preenchido", element=None)
        except Exception as e:
            return BrowserActionResult(success=False, message="", error=str(e))

    async def _find_authorized_element(
        self,
        tab_id: str,
        page_url: str,
        selector: str,
    ) -> Any:
        page = self._get_page(tab_id)
        if page.url != page_url:
            raise PermissionError("A rota autorizada da aba não está mais ativa.")
        element = await page.query_selector(selector)
        if element is None:
            raise ValueError(f"Elemento não encontrado: {selector}")
        if page.url != page_url:
            raise PermissionError("A rota da aba mudou antes da interação.")
        return element

    async def press_key(self, tab_id: str, key: str) -> BrowserActionResult:
        page = self._get_page(tab_id)
        try:
            await page.keyboard.press(key)
            return BrowserActionResult(success=True, message=f"Tecla {key} pressionada", element=None)
        except Exception as e:
            return BrowserActionResult(success=False, message="", error=str(e))

    async def extract_text(self, tab_id: str, selector: str) -> BrowserExtractResult:
        try:
            element = await self._find_element(tab_id, selector)
            text = await element.inner_text()
            return BrowserExtractResult(success=True, data=text.strip())
        except Exception as e:
            return BrowserExtractResult(success=False, data="", error=str(e))

    async def extract_attribute(
        self, tab_id: str, selector: str, attribute: str
    ) -> BrowserExtractResult:
        try:
            element = await self._find_element(tab_id, selector)
            value = await element.get_attribute(attribute)
            return BrowserExtractResult(success=True, data=value or "")
        except Exception as e:
            return BrowserExtractResult(success=False, data="", error=str(e))

    async def extract_multiple(
        self, tab_id: str, selector: str, attributes: list[str]
    ) -> BrowserExtractResult:
        page = self._get_page(tab_id)
        try:
            elements = await page.query_selector_all(selector)
            results = []
            for element in elements:
                data = {}
                for attr in attributes:
                    if attr == "text":
                        data[attr] = (await element.inner_text()).strip()
                    else:
                        data[attr] = await element.get_attribute(attr) or ""
                results.append(data)
            return BrowserExtractResult(success=True, data=results)
        except Exception as e:
            return BrowserExtractResult(success=False, data=[], error=str(e))

    async def wait_for_selector(
        self, tab_id: str, selector: str, timeout: float = 30.0
    ) -> BrowserActionResult:
        page = self._get_page(tab_id)
        try:
            await page.wait_for_selector(selector, timeout=timeout * 1000)
            return BrowserActionResult(success=True, message="Elemento encontrado", element=None)
        except Exception as e:
            return BrowserActionResult(success=False, message="", error=str(e))

    async def get_page_snapshot(self, tab_id: str) -> BrowserPageSnapshot:
        page = self._get_page(tab_id)
        tabs = await self.list_tabs()
        url = page.url
        title = await page.title()
        selected_text = ""
        try:
            selected_text = await page.evaluate("window.getSelection().toString()") or None
        except Exception:
            pass
        viewport = await page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
        return BrowserPageSnapshot(
            url=url,
            title=title,
            tabs=tabs,
            selected_text=selected_text,
            viewport=viewport,
        )

    async def execute_script(self, tab_id: str, script: str) -> BrowserExtractResult:
        page = self._get_page(tab_id)
        try:
            result = await page.evaluate(script)
            return BrowserExtractResult(success=True, data=result)
        except Exception as e:
            return BrowserExtractResult(success=False, data=None, error=str(e))

    async def screenshot(self, tab_id: str, full_page: bool = False) -> bytes:
        page = self._get_page(tab_id)
        return await page.screenshot(full_page=full_page)
