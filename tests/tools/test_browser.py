"""Testes de segurança para Browser Agent."""

import asyncio
import pytest
from pathlib import Path

from jarvis.browser.models import BrowserTab
from jarvis.browser.service import BrowserService
from jarvis.tools.browser import browser_tools
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor


class FakeBrowserBackend:
    """Backend falso para testes unitários."""

    def __init__(self) -> None:
        self.tabs: dict[str, BrowserTab] = {}
        self.tab_counter = 0
        self.closed_tabs: list[str] = []
        self.navigations: list[tuple[str, str]] = []
        self.clicks: list[tuple[str, str]] = []
        self.types: list[tuple[str, str, str]] = []
        self.fills: list[tuple[str, str, str]] = []
        self.key_presses: list[tuple[str, str]] = []
        self.extractions: list[tuple[str, str]] = []
        self.multi_extractions: list[tuple[str, str, list[str]]] = []
        self.waits: list[tuple[str, str, float]] = []
        self.snapshots: list[str] = []
        self.scripts: list[tuple[str, str]] = []
        self.screenshots: list[tuple[str, bool]] = []
        self._launched = False
        self.should_fail = False

    async def launch(self, headless: bool = False) -> None:
        self._launched = True

    async def close(self) -> None:
        self._launched = False
        self.tabs.clear()

    async def list_tabs(self) -> tuple[BrowserTab, ...]:
        if not self._launched:
            raise RuntimeError("Navegador não iniciado. Chame launch() primeiro.")
        return tuple(self.tabs.values())

    async def new_tab(self, url: str = "about:blank") -> BrowserTab:
        if not self._launched:
            raise RuntimeError("Navegador não iniciado. Chame launch() primeiro.")
        self.tab_counter += 1
        tab_id = f"tab_{self.tab_counter}"
        tab = BrowserTab(id=tab_id, url=url, title=f"Página {self.tab_counter}", is_active=True)
        self.tabs[tab_id] = tab
        return tab

    async def close_tab(self, tab_id: str) -> bool:
        if tab_id in self.tabs:
            del self.tabs[tab_id]
            self.closed_tabs.append(tab_id)
            return True
        return False

    async def switch_tab(self, tab_id: str) -> BrowserTab:
        if tab_id not in self.tabs:
            raise ValueError(f"Aba não encontrada: {tab_id}")
        for tid, tab in self.tabs.items():
            self.tabs[tid] = BrowserTab(
                id=tab.id, url=tab.url, title=tab.title, is_active=(tid == tab_id)
            )
        return self.tabs[tab_id]

    async def navigate(self, operation):
        from jarvis.browser.models import BrowserNavigationResult
        operation.require_executing()
        operation.claim_effect()
        tab_id = operation.tab_id
        url = operation.url
        if tab_id not in self.tabs:
            raise ValueError(f"Aba não encontrada: {tab_id}")
        self.tabs[tab_id] = BrowserTab(
            id=tab_id, url=url, title=f"Página {url}", is_active=True
        )
        self.navigations.append((tab_id, url))
        return BrowserNavigationResult(url=url, title=f"Página {url}", success=True)

    async def go_back(self, tab_id: str):
        from jarvis.browser.models import BrowserNavigationResult
        return BrowserNavigationResult(url="", title="", success=True)

    async def go_forward(self, tab_id: str):
        from jarvis.browser.models import BrowserNavigationResult
        return BrowserNavigationResult(url="", title="", success=True)

    async def reload(self, tab_id: str):
        from jarvis.browser.models import BrowserNavigationResult
        return BrowserNavigationResult(url="", title="", success=True)

    async def click(self, tab_id: str, selector: str):
        from jarvis.browser.models import BrowserActionResult
        self.clicks.append((tab_id, selector))
        return BrowserActionResult(success=True, message="Clique realizado")

    async def type_text(self, operation):
        from jarvis.browser.models import BrowserActionResult
        operation.require_executing()
        operation.claim_effect()
        self.types.append(
            (operation.tab_id, operation.selector, operation.text)
        )
        return BrowserActionResult(success=True, message="Texto digitado")

    async def fill(self, operation):
        from jarvis.browser.models import BrowserActionResult
        operation.require_executing()
        operation.claim_effect()
        self.fills.append(
            (operation.tab_id, operation.selector, operation.text)
        )
        return BrowserActionResult(success=True, message="Campo preenchido")

    async def press_key(self, tab_id: str, key: str):
        from jarvis.browser.models import BrowserActionResult
        self.key_presses.append((tab_id, key))
        return BrowserActionResult(success=True, message=f"Tecla {key} pressionada")

    async def extract_text(self, tab_id: str, selector: str):
        from jarvis.browser.models import BrowserExtractResult
        self.extractions.append((tab_id, selector))
        return BrowserExtractResult(success=True, data="texto extraído")

    async def extract_attribute(self, tab_id: str, selector: str, attribute: str):
        from jarvis.browser.models import BrowserExtractResult
        return BrowserExtractResult(success=True, data="valor")

    async def extract_multiple(self, tab_id: str, selector: str, attributes: list[str]):
        from jarvis.browser.models import BrowserExtractResult
        self.multi_extractions.append((tab_id, selector, attributes))
        return BrowserExtractResult(success=True, data=[{"text": "item1"}, {"text": "item2"}])

    async def wait_for_selector(self, tab_id: str, selector: str, timeout: float = 30.0):
        from jarvis.browser.models import BrowserActionResult
        self.waits.append((tab_id, selector, timeout))
        return BrowserActionResult(success=True, message="Elemento encontrado")

    async def get_page_snapshot(self, tab_id: str):
        from jarvis.browser.models import BrowserPageSnapshot
        self.snapshots.append(tab_id)
        return BrowserPageSnapshot(
            url="https://example.com",
            title="Test Page",
            tabs=(),
            selected_text=None,
            viewport={"width": 1280, "height": 720},
        )

    async def execute_script(self, tab_id: str, script: str):
        from jarvis.browser.models import BrowserExtractResult
        self.scripts.append((tab_id, script))
        return BrowserExtractResult(success=True, data="resultado")

    async def screenshot(self, tab_id: str, full_page: bool = False) -> bytes:
        self.screenshots.append((tab_id, full_page))
        return b"fake_screenshot"


def build_service(enabled: bool = True, **kwargs) -> BrowserService:
    backend = FakeBrowserBackend()
    policy = CapabilityPolicy({Capability.BROWSER: enabled})
    return BrowserService(backend, capability_policy=policy, **kwargs)


def build_executor(tool, enabled: bool = True, tmp_path: Path | None = None) -> ToolExecutor:
    audit_path = tmp_path / "audit.log" if tmp_path else Path("audit.log")
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(audit_path),
        capability_policy=CapabilityPolicy({Capability.BROWSER: enabled}),
    )
    return executor


class TestBrowserCapabilityGating:
    """Testes de bloqueio por capability."""

    def test_browser_disabled_blocks_all_tools_before_execution(self) -> None:
        service = build_service(enabled=False)
        for tool in browser_tools(service):
            executor = build_executor(tool, enabled=False)
            result = executor.execute_with_result(tool)
            assert result.status == "security_blocked", f"{tool.name} deveria ser bloqueado"

    def test_browser_disabled_blocks_service_methods(self) -> None:
        service = build_service(enabled=False)

        async def test():
            with pytest.raises(PermissionError, match="browser"):
                await service.list_tabs()

        asyncio.run(test())


class TestBrowserDomainValidation:
    """Testes de validação de domínio (allowlist/blocklist)."""

    def test_allowed_domains_allows_navigation(self) -> None:
        service = build_service(
            enabled=True,
            allowed_domains=["example.com", "github.com"],
        )
        # Não deve lançar exceção para domínio permitido
        service._check_domain("https://example.com/page")
        service._check_domain("https://github.com/user/repo")

    def test_allowed_domains_blocks_other_domains(self) -> None:
        service = build_service(
            enabled=True,
            allowed_domains=["example.com"],
        )
        with pytest.raises(PermissionError, match="não permitido"):
            service._check_domain("https://google.com")
        with pytest.raises(PermissionError, match="não permitido"):
            service._check_domain("https://malicious.com")

    def test_blocked_domains_blocks_navigation(self) -> None:
        service = build_service(
            enabled=True,
            blocked_domains=["malicious.com", "phishing.net"],
        )
        with pytest.raises(PermissionError, match="bloqueado"):
            service._check_domain("https://malicious.com/page")
        with pytest.raises(PermissionError, match="bloqueado"):
            service._check_domain("http://phishing.net/login")

    def test_blocked_domains_allows_other_domains(self) -> None:
        service = build_service(
            enabled=True,
            blocked_domains=["malicious.com"],
        )
        service._check_domain("https://example.com")
        service._check_domain("https://github.com")

    def test_allowlist_priority_over_blocklist(self) -> None:
        service = build_service(
            enabled=True,
            allowed_domains=["example.com"],
            blocked_domains=["example.com"],  # Mesmo domínio nas duas listas
        )
        # Allowlist deve ter prioridade (ou pelo menos não deve falhar silenciosamente)
        # O comportamento atual é permitir se estiver na allowlist
        service._check_domain("https://example.com")


class TestBrowserTabLimits:
    """Testes de limite de abas."""

    def test_max_tabs_prevents_new_tab(self) -> None:
        service = build_service(enabled=True, max_tabs=2)
        backend = service.backend

        async def test():
            await service.launch()
            await service.new_tab("https://example.com")
            await service.new_tab("https://example.com/page2")
            with pytest.raises(RuntimeError, match="Limite de 2 abas atingido"):
                await service.new_tab("https://example.com/page3")

        asyncio.run(test())

    def test_close_tab_allows_new_tab(self) -> None:
        service = build_service(enabled=True, max_tabs=2)
        backend = service.backend

        async def test():
            await service.launch()
            await service.new_tab("https://example.com")
            await service.new_tab("https://example.com/page2")
            await service.close_tab("tab_1")
            tab = await service.new_tab("https://example.com/page3")
            assert tab.id == "tab_3"

        asyncio.run(test())

    def test_switch_to_nonexistent_tab_fails(self) -> None:
        service = build_service(enabled=True)

        async def test():
            await service.launch()
            with pytest.raises(ValueError, match="não encontrada"):
                await service.switch_tab("tab_999")

        asyncio.run(test())

    def test_close_nonexistent_tab_returns_false(self) -> None:
        service = build_service(enabled=True)

        async def test():
            await service.launch()
            result = await service.close_tab("tab_999")
            assert result is False

        asyncio.run(test())


class TestBrowserTabPreflight:
    """Testes de preflight para validação de abas."""

    def test_list_tabs_tool_preflight_succeeds(self) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_list_tabs")
        tool.preflight()  # Não deve lançar

    def test_new_tab_preflight_checks_tab_limit(self) -> None:
        service = build_service(enabled=True, max_tabs=1)
        tool = next(t for t in browser_tools(service) if t.name == "browser_new_tab")

        async def test():
            await service.launch()
            await service.new_tab("https://example.com")
            with pytest.raises(RuntimeError, match="Limite de 1 abas atingido"):
                tool.preflight(url="https://another.com")

        asyncio.run(test())

    def test_close_tab_preflight_validates_tab_exists(self) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_close_tab")
        
        async def test():
            await service.launch()
            with pytest.raises(ValueError, match="não encontrada"):
                tool.preflight(tab_id="tab_123")

        asyncio.run(test())

    def test_switch_tab_preflight_validates_tab_exists(self) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_switch_tab")
        
        async def test():
            await service.launch()
            with pytest.raises(ValueError, match="não encontrada"):
                tool.preflight(tab_id="tab_999")

        asyncio.run(test())

    def test_navigate_preflight_checks_domain_and_tab(self) -> None:
        service = build_service(enabled=True, allowed_domains=["example.com"])
        tool = next(t for t in browser_tools(service) if t.name == "browser_navigate")

        # Domínio não permitido
        with pytest.raises(PermissionError, match="não permitido"):
            tool.preflight(url="https://google.com")

        # Domínio permitido, aba inexistente
        async def test():
            await service.launch()
            await service.new_tab("https://example.com")
            with pytest.raises(ValueError, match="não encontrada"):
                tool.preflight(url="https://example.com", tab_id="tab_999")
        asyncio.run(test())


class TestBrowserKeyAndScriptAllowlist:
    """Testes de allowlist de teclas e scripts."""

    def test_allowed_keys_accepted(self) -> None:
        service = build_service(enabled=True)
        allowed = ["Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "F1", "F12"]
        for key in allowed:
            service._check_key(key)  # Não deve lançar

    def test_disallowed_keys_rejected(self) -> None:
        service = build_service(enabled=True)
        with pytest.raises(ValueError, match="não permitida"):
            service._check_key("Control")
        with pytest.raises(ValueError, match="não permitida"):
            service._check_key("Alt")
        with pytest.raises(ValueError, match="não permitida"):
            service._check_key("Shift")

    def test_allowed_scripts_accepted(self) -> None:
        service = build_service(enabled=True)
        allowed = [
            "document.querySelector('button')",
            "document.getElementById('test')",
            "document.title",
            "window.location.href",
            "window.innerWidth",
            "window.innerHeight",
        ]
        for script in allowed:
            service._check_script(script)  # Não deve lançar

    def test_disallowed_scripts_rejected(self) -> None:
        service = build_service(enabled=True)
        disallowed = [
            "eval('malicious')",
            "document.cookie",
            "localStorage.getItem('secret')",
            "fetch('https://evil.com')",
            "document.body.innerHTML = 'hacked'",
        ]
        for script in disallowed:
            with pytest.raises(PermissionError, match="não permitido"):
                service._check_script(script)


class TestBrowserPreparedTools:
    """Testes das Tools com contexto preparado."""

    def test_prepared_browser_tool_prepares_context(self) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_click")

        async def test():
            await service.launch()
            await service.new_tab("https://example.com")
            prep = tool.prepare(selector="button", tab_id="tab_1")
            assert prep.target == "browser:tab:tab_1:button"
            assert prep.context is not None
            assert prep.context.tab_id == "tab_1"
            assert prep.context.selector == "button"

        asyncio.run(test())

    def test_prepared_browser_tool_preflight_validates_tab(self) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_click")

        async def test():
            await service.launch()
            # Preflight com nenhuma aba aberta deve falhar
            with pytest.raises(RuntimeError, match="Nenhuma aba aberta"):
                tool.preflight(selector="button", tab_id="tab_999")

        asyncio.run(test())

    def test_prepared_browser_tool_refresh_prepared_preserves_context(self) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_click")

        async def test():
            await service.launch()
            await service.new_tab("https://example.com")
            prep = tool.prepare(selector="button", tab_id="tab_1")
            refreshed = tool.refresh_prepared(prep)
            assert refreshed.context is not None
            assert refreshed.context.tab_id == "tab_1"
            assert refreshed.context.selector == "button"
            assert refreshed.binding_digest == prep.binding_digest

        asyncio.run(test())


class TestBrowserAuditAndPrivacy:
    """Testes de auditoria e privacidade."""

    def test_browser_tools_expose_no_sensitive_data_in_result(self) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_list_tabs")

        async def test():
            await service.launch()
            await service.new_tab("https://example.com")
            result = tool.execute()
            assert "https://example.com" in result
            assert "tab_1" in result
            # Não deve expor estruturas internas
            assert "id=" not in result.lower()

        asyncio.run(test())

    def test_extract_text_returns_only_text(self) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_extract_text")

        async def test():
            await service.launch()
            await service.new_tab("https://example.com")
            # Use o serviço diretamente para evitar conflitos de event loop
            result = await service.extract_text(tab_id="tab_1", selector="h1")
            assert result.success
            assert "texto extraído" in result.data
            assert "selector" not in result.data.lower()

        asyncio.run(test())

    def test_executor_audits_fixed_target_without_raw_data(self, tmp_path: Path) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_navigate")
        audit_path = tmp_path / "audit.log"
        executor = ToolExecutor(
            SafetyManager(),
            AuditLogger(audit_path),
            capability_policy=CapabilityPolicy({Capability.BROWSER: True}),
        )

        # Launch browser and create tab synchronously
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(service.launch())
            tab = loop.run_until_complete(service.new_tab("https://example.com"))
            tab_id = tab.id  # Use the actual tab ID returned
        finally:
            loop.close()

        # Now test executor (synchronous call)
        result = executor.execute_with_result(tool, url="https://example.com", tab_id=tab_id)
        assert result.status == "success"
        audit = audit_path.read_text(encoding="utf-8")
        assert f"target=browser:tab:{tab_id}:https://example.com" in audit
        assert "secret" not in audit


class TestBrowserExecutorIntegration:
    """Testes de integração com ToolExecutor."""

    def test_executor_blocks_disabled_browser(self, tmp_path: Path) -> None:
        service = build_service(enabled=False)
        tool = next(t for t in browser_tools(service) if t.name == "browser_list_tabs")
        executor = build_executor(tool, enabled=False, tmp_path=tmp_path)

        result = executor.execute_with_result(tool)
        assert result.status == "security_blocked"

    def test_executor_blocks_nonexistent_tab(self, tmp_path: Path) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_click")
        executor = build_executor(tool, enabled=True, tmp_path=tmp_path)

        # Launch browser and create tab synchronously
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(service.launch())
            tab = loop.run_until_complete(service.new_tab("https://example.com"))
            tab_id = tab.id
        finally:
            loop.close()

        # Test executor (synchronous call)
        result = executor.execute_with_result(tool, selector="button", tab_id="tab_999")
        assert result.status == "error"
        assert "não encontrada" in result.content.lower()

    def test_executor_blocks_invalid_selector(self, tmp_path: Path) -> None:
        service = build_service(enabled=True)
        tool = next(t for t in browser_tools(service) if t.name == "browser_click")
        executor = build_executor(tool, enabled=True, tmp_path=tmp_path)

        # Launch browser and create tab synchronously
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(service.launch())
            tab = loop.run_until_complete(service.new_tab("https://example.com"))
            tab_id = tab.id
        finally:
            loop.close()

        # Selector vazio deveria falhar no preflight
        result = executor.execute_with_result(tool, selector="", tab_id=tab_id)
        assert result.status == "error"


class TestBrowserServiceLaunchRequired:
    """Testes que métodos exigem launch()."""

    def test_methods_fail_without_launch(self) -> None:
        service = build_service(enabled=True)
        # Não chama launch()

        async def test_methods():
            methods = [
                service.list_tabs(),
                service.new_tab("https://example.com"),
                service.close_tab("tab_1"),
                service.switch_tab("tab_1"),
                service.go_back("tab_1"),
                service.go_forward("tab_1"),
                service.reload("tab_1"),
                service.click("tab_1", "button"),
                service.press_key("tab_1", "Enter"),
                service.extract_text("tab_1", "h1"),
                service.extract_attribute("tab_1", "a", "href"),
                service.extract_multiple("tab_1", "a", ["href", "text"]),
                service.wait_for_selector("tab_1", "button"),
                service.get_page_snapshot("tab_1"),
                service.execute_script("tab_1", "document.title"),
                service.screenshot("tab_1"),
            ]

            for coro in methods:
                with pytest.raises(RuntimeError, match="não iniciado"):
                    await coro

            for coro in (
                service.navigate("not-authorized"),  # type: ignore[arg-type]
                service.type_text("not-authorized"),  # type: ignore[arg-type]
                service.fill("not-authorized"),  # type: ignore[arg-type]
            ):
                with pytest.raises(PermissionError, match="autorizado"):
                    await coro

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(test_methods())
        finally:
            loop.close()


class TestBrowserTabIdentity:
    """Testes de identidade e binding de aba."""

    def test_prepared_target_binding_identity(self) -> None:
        from jarvis.browser.models import PreparedBrowserTarget

        target = PreparedBrowserTarget(
            public_target="browser:tab:tab_1:button",
            tab_id="tab_1",
            selector="button",
            url="https://example.com",
        )
        identity = target.binding_identity()
        assert "tab_1" in identity
        assert "button" in identity
        assert "example.com" in identity


class TestBrowserConfiguration:
    """Testes de configuração via ambiente e settings."""

    def test_browser_config_from_environment(self, monkeypatch) -> None:
        from jarvis.config import JarvisConfig

        monkeypatch.setenv("JARVIS_BROWSER", "1")
        monkeypatch.setenv("JARVIS_BROWSER_ALLOWED_DOMAINS", "example.com,github.com")
        monkeypatch.setenv("JARVIS_BROWSER_BLOCKED_DOMAINS", "evil.com")
        monkeypatch.setenv("JARVIS_BROWSER_MAX_TABS", "5")

        config = JarvisConfig()
        assert config.browser_enabled is True
        assert config.browser_allowed_domains == ["example.com", "github.com"]
        assert config.browser_blocked_domains == ["evil.com"]
        assert config.browser_max_tabs == 5


# Fixtures
@pytest.fixture
def tmp_path_fixture(tmp_path: Path) -> Path:
    return tmp_path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
