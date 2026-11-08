from __future__ import annotations

from typing import Any

from jarvis.safe_browser.models import (
    AuthorizedBrowserLaunch,
    PreparedBrowserLaunch,
)
from jarvis.safe_browser.registry import DESTINATIONS, SEARCH_PROVIDERS
from jarvis.safe_browser.service import SafeBrowserService
from jarvis.security.authorization import AuthorizationReceipt
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


class SafeBrowserStatusTool(Tool):
    name = "safe_browser_status"
    description = "Consulta se o Windows possui navegador padrão utilizável."
    risk = RiskLevel.READ_ONLY
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, service: SafeBrowserService) -> None: self.service = service
    def get_target(self, **kwargs: Any) -> str: return "browser:default_association"
    def execute(self, **kwargs: Any) -> str: return self.service.status()


class SafeBrowserDestinationsTool(Tool):
    name = "safe_browser_destinations"
    description = "Lista IDs de destinos web fixos disponíveis."
    risk = RiskLevel.READ_ONLY
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, service: SafeBrowserService) -> None: self.service = service
    def get_target(self, **kwargs: Any) -> str: return "browser:destination_registry"
    def execute(self, **kwargs: Any) -> str: return self.service.destinations()


class SafeBrowserOpenTool(Tool):
    name = "safe_browser_open"
    description = "Entrega ao navegador padrão somente um destino HTTPS registrado."
    risk = RiskLevel.REVERSIBLE_LOCAL
    parameters = {
        "type": "object",
        "properties": {"destination": {"type": "string", "enum": sorted(DESTINATIONS)}},
        "required": ["destination"],
        "additionalProperties": False,
    }

    def __init__(self, service: SafeBrowserService) -> None: self.service = service
    def get_target(self, **kwargs: Any) -> str: return self.prepare(**kwargs).target
    def prepare(self, **kwargs: Any) -> ToolPreparation:
        prepared = self.service.prepare_destination(str(kwargs["destination"]))
        return ToolPreparation(
            f"browser:destination:{prepared.request.destination_id}",
            prepared, prepared.binding_digest(),
        )
    def preflight_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> None:
        self.service.revalidate(self._prepared(preparation))
    def execute(self, **kwargs: Any) -> str:
        return self.execute_prepared(self.prepare(**kwargs), **kwargs)
    def execute_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> str:
        return self.service.execute(self._prepared(preparation))

    @staticmethod
    def _prepared(preparation: ToolPreparation) -> PreparedBrowserLaunch:
        if not isinstance(preparation.context, PreparedBrowserLaunch):
            raise RuntimeError("Preparação de destino ausente.")
        return preparation.context


class SafeWebSearchTool(Tool):
    name = "safe_web_search"
    description = "Envia termo confirmado a um provedor de busca com template fixo."
    risk = RiskLevel.EXTERNAL_EFFECT
    requires_authorization_receipt = True
    confirmation_timeout_seconds = 30.0
    parameters = {
        "type": "object",
        "properties": {
            "provider": {"type": "string", "enum": sorted(SEARCH_PROVIDERS)},
            "term": {"type": "string"},
        },
        "required": ["provider", "term"],
        "additionalProperties": False,
    }

    def __init__(self, service: SafeBrowserService) -> None: self.service = service
    def get_target(self, **kwargs: Any) -> str: return self.prepare(**kwargs).target
    def prepare(self, **kwargs: Any) -> ToolPreparation:
        prepared = self.service.prepare_search(
            str(kwargs["provider"]), str(kwargs["term"])
        )
        return ToolPreparation(
            self.service.search_target(prepared), prepared,
            prepared.binding_digest(),
        )
    def preflight_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> None:
        self.service.revalidate(self._prepared(preparation))
    def bind_authorized(self, preparation: ToolPreparation,
                        authorization: object | None = None) -> ToolPreparation:
        if not isinstance(authorization, AuthorizationReceipt):
            raise PermissionError("Comprovante de busca ausente.")
        if not authorization.claim(self.name, preparation.target,
                                   preparation.binding_digest):
            raise PermissionError("Comprovante de busca inválido ou reutilizado.")
        return ToolPreparation(
            preparation.target,
            AuthorizedBrowserLaunch(self._prepared(preparation)),
            preparation.binding_digest,
        )
    def execute(self, **kwargs: Any) -> str:
        raise PermissionError("Busca exige confirmação vinculada.")
    def execute_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> str:
        raise PermissionError("Busca preparada ainda não foi autorizada.")
    def execute_authorized(self, preparation: ToolPreparation) -> str:
        context = preparation.context
        if not isinstance(context, AuthorizedBrowserLaunch):
            raise PermissionError("Busca autorizada ausente.")
        return self.service.execute(context.prepared)
    def confirmation_prompt(self, preparation: ToolPreparation) -> str:
        prepared = self._prepared(preparation)
        return (
            f'Vou enviar o termo “{prepared.term}” ao provedor Google. '
            "Autoriza esta busca na web?"
        )

    @staticmethod
    def _prepared(preparation: ToolPreparation) -> PreparedBrowserLaunch:
        if not isinstance(preparation.context, PreparedBrowserLaunch):
            raise RuntimeError("Preparação de busca ausente.")
        return preparation.context


def safe_browser_tools(service: SafeBrowserService) -> list[Tool]:
    return [
        SafeBrowserStatusTool(service), SafeBrowserDestinationsTool(service),
        SafeBrowserOpenTool(service), SafeWebSearchTool(service),
    ]
