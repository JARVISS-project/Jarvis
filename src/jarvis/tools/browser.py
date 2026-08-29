"""Tools de navegador deliberadamente pequenas e com validações de segurança."""

import re
from typing import Any

from jarvis.browser.models import PreparedBrowserTarget
from jarvis.browser.service import BrowserService
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


class _PreparedBrowserTool(Tool):
    service: BrowserService

    def prepare(self, **kwargs: Any) -> ToolPreparation:
        tab_id = str(kwargs.get("tab_id", "active"))
        selector = kwargs.get("selector")
        url = kwargs.get("url")
        public_target = f"browser:tab:{tab_id}"
        if selector:
            public_target += f":{selector}"
        elif url:
            public_target += f":{url}"
        prepared = PreparedBrowserTarget(
            public_target=public_target,
            tab_id=tab_id,
            selector=selector,
            url=url,
        )
        return ToolPreparation(prepared.public_target, prepared)

    def get_target(self, **kwargs: Any) -> str:
        return self.prepare(**kwargs).target

    def preflight(self, **kwargs: Any) -> None:
        tab_id = str(kwargs.get("tab_id", "active"))
        tabs = self.service.list_tabs_sync(tab_id)
        if not tabs:
            raise RuntimeError("Nenhuma aba aberta.")
        # Verifica se a aba existe
        tab_exists = any(t.id == tab_id for t in tabs)
        if not tab_exists:
            raise ValueError(f"Aba não encontrada: {tab_id}")

    def preflight_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> None:
        context = preparation.context
        if not isinstance(context, PreparedBrowserTarget):
            raise RuntimeError("Contexto preparado do navegador ausente.")
        tabs = self.service.list_tabs_sync(context.tab_id)
        if not any(t.id == context.tab_id for t in tabs):
            raise ValueError(f"Aba não encontrada: {context.tab_id}")

    def refresh_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> ToolPreparation:
        context = preparation.context
        if not isinstance(context, PreparedBrowserTarget):
            raise RuntimeError("Contexto preparado do navegador ausente.")
        # Para browser, não há refresh de geometria como no desktop
        return ToolPreparation(
            preparation.target,
            context,
            preparation.binding_digest,
        )

    @staticmethod
    def _prepared_browser(
        preparation: ToolPreparation,
    ) -> PreparedBrowserTarget:
        context = preparation.context
        if not isinstance(context, PreparedBrowserTarget):
            raise RuntimeError("Contexto preparado do navegador ausente.")
        return context


def _get_event_loop():
    """Obtém um event loop válido, criando um novo se necessário ou se o atual estiver fechado."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Event loop is closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _make_sync_method(async_method):
    """Wrapper para chamar método assíncrono de forma síncrona (para preflight)."""
    def wrapper(self, *args, **kwargs):
        loop = _get_event_loop()
        return loop.run_until_complete(async_method(self, *args, **kwargs))
    return wrapper


class BrowserListTabsTool(_PreparedBrowserTool):
    name = "browser_list_tabs"
    description = (
        "Lista todas as abas abertas no navegador com seus IDs, URLs e títulos."
    )
    risk = RiskLevel.SAFE
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        return "browser:tabs"

    def preflight(self, **kwargs: Any) -> None:
        # browser_list_tabs não requer abas existentes - apenas lista o que houver
        pass

    def execute(self, **kwargs: Any) -> str:
        tabs = self.service.list_tabs_sync()
        if not tabs:
            return "Nenhuma aba aberta."
        lines = []
        for tab in tabs:
            active = " (ativa)" if tab.is_active else ""
            lines.append(f"- [{tab.id}] {tab.title} - {tab.url}{active}")
        return "\n".join(lines)

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserNewTabTool(_PreparedBrowserTool):
    name = "browser_new_tab"
    description = (
        "Abre uma nova aba no navegador. Opcionalmente navega para uma URL."
    )
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL para navegar (opcional, padrão: about:blank)",
                "default": "about:blank",
            }
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        url = kwargs.get("url", "about:blank")
        return f"browser:new_tab:{url}"

    def preflight(self, **kwargs: Any) -> None:
        tabs = self.service.list_tabs_sync()
        if len(tabs) >= self.service.max_tabs:
            raise RuntimeError(f"Limite de {self.service.max_tabs} abas atingido.")
        url = kwargs.get("url", "about:blank")
        if url != "about:blank":
            self.service._check_domain(url)

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        url = kwargs.get("url", "about:blank")
        loop = _get_event_loop()
        tab = loop.run_until_complete(self.service.new_tab(url))
        return f'Aba criada: [{tab.id}] {tab.title} - {tab.url}'

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserCloseTabTool(_PreparedBrowserTool):
    name = "browser_close_tab"
    description = "Fecha uma aba do navegador pelo seu ID."
    risk = RiskLevel.MEDIUM
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba a fechar.",
            }
        },
        "required": ["tab_id"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        return f"browser:tab:{kwargs['tab_id']}"

    def preflight(self, **kwargs: Any) -> None:
        tab_id = str(kwargs["tab_id"])
        tabs = self.service.list_tabs_sync()
        if not any(t.id == tab_id for t in tabs):
            raise ValueError(f"Aba não encontrada: {tab_id}")

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        tab_id = str(kwargs["tab_id"])
        loop = _get_event_loop()
        closed = loop.run_until_complete(self.service.close_tab(tab_id))
        if closed:
            return f"Aba {tab_id} fechada."
        return f"Aba {tab_id} não encontrada."

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserSwitchTabTool(_PreparedBrowserTool):
    name = "browser_switch_tab"
    description = "Muda para uma aba específica pelo seu ID."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba para ativar.",
            }
        },
        "required": ["tab_id"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        return f"browser:tab:{kwargs['tab_id']}"

    def preflight(self, **kwargs: Any) -> None:
        tab_id = str(kwargs["tab_id"])
        tabs = self.service.list_tabs_sync()
        if not any(t.id == tab_id for t in tabs):
            raise ValueError(f"Aba não encontrada: {tab_id}")

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        tab_id = str(kwargs["tab_id"])
        loop = _get_event_loop()
        tab = loop.run_until_complete(self.service.switch_tab(tab_id))
        return f'Aba ativada: [{tab.id}] {tab.title} - {tab.url}'

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserNavigateTool(_PreparedBrowserTool):
    name = "browser_navigate"
    description = "Navega para uma URL na aba ativa ou especificada."
    risk = RiskLevel.MEDIUM
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL para navegar.",
            },
            "tab_id": {
                "type": "string",
                "description": "ID da aba (opcional, usa a ativa se omitido).",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        tab_id = kwargs.get("tab_id", "active")
        return f"browser:navigate:{tab_id}:{kwargs['url']}"

    def preflight(self, **kwargs: Any) -> None:
        self.service._check_domain(kwargs["url"])
        tab_id = kwargs.get("tab_id", "active")
        if tab_id != "active":
            tabs = self.service.list_tabs_sync()
            if not any(t.id == tab_id for t in tabs):
                raise ValueError(f"Aba não encontrada: {tab_id}")

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        url = kwargs["url"]
        tab_id = kwargs.get("tab_id", "active")
        if tab_id == "active":
            tabs = self.service.list_tabs_sync()
            active_tab = next((t for t in tabs if t.is_active), None)
            if active_tab is None:
                raise RuntimeError("Nenhuma aba ativa.")
            tab_id = active_tab.id
        loop = _get_event_loop()
        result = loop.run_until_complete(self.service.navigate(tab_id, url))
        if result.success:
            return f'Navegou para {result.url} - {result.title}'
        return f'Erro ao navegar: {result.error}'

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserClickTool(_PreparedBrowserTool):
    name = "browser_click"
    description = "Clica em um elemento identificado por seletor CSS."
    risk = RiskLevel.MEDIUM
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba (opcional, usa a ativa se omitido).",
            },
            "selector": {
                "type": "string",
                "description": "Seletor CSS do elemento a clicar.",
            },
        },
        "required": ["selector"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def preflight(self, **kwargs: Any) -> None:
        super().preflight(**kwargs)
        selector = kwargs.get("selector", "")
        if not selector or not selector.strip():
            raise ValueError("Seletor não pode ser vazio.")

    def preflight_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> None:
        super().preflight_prepared(preparation, **kwargs)
        selector = kwargs.get("selector", "")
        if not selector or not selector.strip():
            raise ValueError("Seletor não pode ser vazio.")

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        tab_id = kwargs.get("tab_id", "active")
        if tab_id == "active":
            tabs = self.service.list_tabs_sync()
            active_tab = next((t for t in tabs if t.is_active), None)
            if active_tab is None:
                raise RuntimeError("Nenhuma aba ativa.")
            tab_id = active_tab.id
        selector = kwargs["selector"]
        loop = _get_event_loop()
        result = loop.run_until_complete(self.service.click(tab_id, selector))
        if result.success:
            return f"Clique realizado em {selector}"
        return f"Erro ao clicar: {result.error}"

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserTypeTool(_PreparedBrowserTool):
    name = "browser_type"
    description = "Digita texto em um elemento identificado por seletor CSS."
    risk = RiskLevel.MEDIUM
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba (opcional, usa a ativa se omitido).",
            },
            "selector": {
                "type": "string",
                "description": "Seletor CSS do elemento.",
            },
            "text": {
                "type": "string",
                "description": "Texto a digitar.",
            },
        },
        "required": ["selector", "text"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        tab_id = kwargs.get("tab_id", "active")
        if tab_id == "active":
            tabs = self.service.list_tabs_sync()
            active_tab = next((t for t in tabs if t.is_active), None)
            if active_tab is None:
                raise RuntimeError("Nenhuma aba ativa.")
            tab_id = active_tab.id
        selector = kwargs["selector"]
        text = kwargs["text"]
        loop = _get_event_loop()
        result = loop.run_until_complete(self.service.type_text(tab_id, selector, text))
        if result.success:
            return f"Texto digitado em {selector}"
        return f"Erro ao digitar: {result.error}"

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserFillTool(_PreparedBrowserTool):
    name = "browser_fill"
    description = "Preenche um campo de formulário identificado por seletor CSS."
    risk = RiskLevel.MEDIUM
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba (opcional, usa a ativa se omitido).",
            },
            "selector": {
                "type": "string",
                "description": "Seletor CSS do campo.",
            },
            "text": {
                "type": "string",
                "description": "Texto a preencher.",
            },
        },
        "required": ["selector", "text"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        tab_id = kwargs.get("tab_id", "active")
        if tab_id == "active":
            tabs = self.service.list_tabs_sync()
            active_tab = next((t for t in tabs if t.is_active), None)
            if active_tab is None:
                raise RuntimeError("Nenhuma aba ativa.")
            tab_id = active_tab.id
        selector = kwargs["selector"]
        text = kwargs["text"]
        loop = _get_event_loop()
        result = loop.run_until_complete(self.service.fill(tab_id, selector, text))
        if result.success:
            return f"Campo preenchido: {selector}"
        return f"Erro ao preencher: {result.error}"

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserExtractTextTool(_PreparedBrowserTool):
    name = "browser_extract_text"
    description = "Extrai o texto de um elemento identificado por seletor CSS."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba (opcional, usa a ativa se omitido).",
            },
            "selector": {
                "type": "string",
                "description": "Seletor CSS do elemento.",
            },
        },
        "required": ["selector"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        tab_id = kwargs.get("tab_id", "active")
        if tab_id == "active":
            tabs = self.service.list_tabs_sync()
            active_tab = next((t for t in tabs if t.is_active), None)
            if active_tab is None:
                raise RuntimeError("Nenhuma aba ativa.")
            tab_id = active_tab.id
        selector = kwargs["selector"]
        loop = _get_event_loop()
        result = loop.run_until_complete(self.service.extract_text(tab_id, selector))
        if result.success:
            return f"Texto extraído: {result.data}"
        return f"Erro ao extrair: {result.error}"

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserExtractMultipleTool(_PreparedBrowserTool):
    name = "browser_extract_multiple"
    description = "Extrai múltiplos elementos e seus atributos."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba (opcional, usa a ativa se omitido).",
            },
            "selector": {
                "type": "string",
                "description": "Seletor CSS dos elementos.",
            },
            "attributes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Lista de atributos a extrair (ex: ['text', 'href', 'src']).",
            },
        },
        "required": ["selector", "attributes"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        import json
        tab_id = kwargs.get("tab_id", "active")
        if tab_id == "active":
            tabs = self.service.list_tabs_sync()
            active_tab = next((t for t in tabs if t.is_active), None)
            if active_tab is None:
                raise RuntimeError("Nenhuma aba ativa.")
            tab_id = active_tab.id
        selector = kwargs["selector"]
        attributes = kwargs["attributes"]
        loop = _get_event_loop()
        result = loop.run_until_complete(
            self.service.extract_multiple(tab_id, selector, attributes)
        )
        if result.success:
            return f"Elementos extraídos: {json.dumps(result.data, ensure_ascii=False)}"
        return f"Erro ao extrair: {result.error}"

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserWaitForSelectorTool(_PreparedBrowserTool):
    name = "browser_wait_for_selector"
    description = "Aguarda um elemento aparecer na página."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba (opcional, usa a ativa se omitido).",
            },
            "selector": {
                "type": "string",
                "description": "Seletor CSS do elemento a aguardar.",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout em segundos (padrão: 30).",
                "default": 30,
            },
        },
        "required": ["selector"],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        tab_id = kwargs.get("tab_id", "active")
        if tab_id == "active":
            tabs = self.service.list_tabs_sync()
            active_tab = next((t for t in tabs if t.is_active), None)
            if active_tab is None:
                raise RuntimeError("Nenhuma aba ativa.")
            tab_id = active_tab.id
        selector = kwargs["selector"]
        timeout = kwargs.get("timeout", 30)
        loop = _get_event_loop()
        result = loop.run_until_complete(
            self.service.wait_for_selector(tab_id, selector, timeout)
        )
        if result.success:
            return f"Elemento encontrado: {selector}"
        return f"Timeout aguardando {selector}: {result.error}"

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        return self.execute(**kwargs)


class BrowserGetSnapshotTool(Tool):
    name = "browser_get_snapshot"
    description = "Obtém um snapshot da página atual (URL, título, abas, texto selecionado)."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "ID da aba (opcional, usa a ativa se omitido).",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        tab_id = kwargs.get("tab_id", "active")
        return f"browser:snapshot:{tab_id}"

    def preflight(self, **kwargs: Any) -> None:
        tab_id = kwargs.get("tab_id", "active")
        if tab_id != "active":
            tabs = self.service.list_tabs_sync()
            if not any(t.id == tab_id for t in tabs):
                raise ValueError(f"Aba não encontrada: {tab_id}")

    def execute(self, **kwargs: Any) -> str:
        import asyncio
        import json
        tab_id = kwargs.get("tab_id", "active")
        if tab_id == "active":
            tabs = self.service.list_tabs_sync()
            active_tab = next((t for t in tabs if t.is_active), None)
            if active_tab is None:
                raise RuntimeError("Nenhuma aba ativa.")
            tab_id = active_tab.id
        loop = _get_event_loop()
        snapshot = loop.run_until_complete(self.service.get_page_snapshot(tab_id))
        return (
            f"Snapshot da página:\n"
            f"  URL: {snapshot.url}\n"
            f"  Título: {snapshot.title}\n"
            f"  Abas: {len(snapshot.tabs)}\n"
            f"  Viewport: {snapshot.viewport}\n"
            f"  Texto selecionado: {snapshot.selected_text or '(vazio)'}"
        )


def browser_tools(service: BrowserService) -> list[Tool]:
    return [
        BrowserListTabsTool(service),
        BrowserNewTabTool(service),
        BrowserCloseTabTool(service),
        BrowserSwitchTabTool(service),
        BrowserNavigateTool(service),
        BrowserClickTool(service),
        BrowserTypeTool(service),
        BrowserFillTool(service),
        BrowserExtractTextTool(service),
        BrowserExtractMultipleTool(service),
        BrowserWaitForSelectorTool(service),
        BrowserGetSnapshotTool(service),
    ]