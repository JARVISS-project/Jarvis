from __future__ import annotations

from hashlib import sha256
from typing import Any

from jarvis.local_control.models import PreparedApplicationAction
from jarvis.local_control.service import APPLICATION_IDS, LocalControlService
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


class _ApplicationTool(Tool):
    action: str

    def __init__(self, service: LocalControlService) -> None:
        self.service = service
        self.parameters = {
            "type": "object",
            "properties": {"application": {"type": "string", "enum": sorted(APPLICATION_IDS)}},
            "required": ["application"],
            "additionalProperties": False,
        }

    def prepare(self, **kwargs: Any) -> ToolPreparation:
        prepared = self.service.prepare_application(str(kwargs["application"]), self.action)
        identity = prepared.instance.identity() if prepared.instance else prepared.application_id
        if prepared.catalog_prepared is not None:
            identity = prepared.catalog_prepared.digest()
        return ToolPreparation(
            f"aplicativo:{prepared.application_id}", prepared,
            sha256(f"{self.action}|{identity}".encode()).hexdigest(),
        )

    def get_target(self, **kwargs: Any) -> str:
        return self.prepare(**kwargs).target

    def preflight_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> None:
        self.service.revalidate(self._context(preparation))

    def refresh_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> ToolPreparation:
        self.preflight_prepared(preparation, **kwargs)
        return preparation

    @staticmethod
    def _context(preparation: ToolPreparation) -> PreparedApplicationAction:
        if not isinstance(preparation.context, PreparedApplicationAction):
            raise RuntimeError("Preparação de aplicativo ausente.")
        return preparation.context


class LocalApplicationOpenTool(_ApplicationTool):
    name = "local_application_open"
    description = "Abre somente um ID de aplicativo da allowlist local, sem argumentos."
    risk = RiskLevel.REVERSIBLE_LOCAL
    action = "open"

    def execute(self, **kwargs: Any) -> str:
        return self.execute_prepared(self.prepare(**kwargs), **kwargs)

    def execute_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> str:
        return self.service.open_prepared(self._context(preparation))


class LocalApplicationStatusTool(_ApplicationTool):
    name = "local_application_status"
    description = "Consulta se um aplicativo permitido está aberto."
    risk = RiskLevel.READ_ONLY
    action = "status"

    def execute(self, **kwargs: Any) -> str:
        state = self.service.state(str(kwargs["application"]))
        return f"{state.application_id} está aberto." if state.is_open else f"{state.application_id} está fechado."


class LocalApplicationFocusTool(_ApplicationTool):
    name = "local_application_focus"
    description = "Traz para frente uma única instância comprovada de aplicativo permitido."
    risk = RiskLevel.REVERSIBLE_LOCAL
    action = "focus"

    def execute(self, **kwargs: Any) -> str:
        return self.execute_prepared(self.prepare(**kwargs), **kwargs)

    def execute_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> str:
        return self.service.focus_prepared(self._context(preparation))


class LocalApplicationCloseTool(_ApplicationTool):
    name = "local_application_close"
    description = "Solicita fechamento gracioso de uma instância comprovada; pode haver trabalho não salvo."
    risk = RiskLevel.SENSITIVE
    requires_authorization_receipt = True
    action = "close"

    def execute(self, **kwargs: Any) -> str:
        raise RuntimeError("Fechamento exige preparação e autorização vinculada.")

    def bind_authorized(self, preparation: ToolPreparation, authorization: object | None = None) -> ToolPreparation:
        if authorization is None:
            raise PermissionError("Autorização vinculada ausente.")
        return preparation

    def execute_authorized(self, preparation: ToolPreparation) -> str:
        return self.service.close_prepared(self._context(preparation))


class LocalVolumeTool(Tool):
    name = "local_volume_control"
    description = "Consulta ou altera volume/mudo com valores enumerados e limitados."
    risk = RiskLevel.REVERSIBLE_LOCAL
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "increase", "decrease", "set", "mute", "unmute"]},
            "percent": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        "required": ["action"], "additionalProperties": False,
    }

    def __init__(self, service: LocalControlService) -> None: self.service = service
    def get_target(self, **kwargs: Any) -> str: return "audio:default_endpoint"
    def preflight(self, **kwargs: Any) -> None:
        action = kwargs["action"]
        percent = kwargs.get("percent")
        if action == "set" and percent is None: raise ValueError("Percentual obrigatório.")
        if action != "set" and percent is not None: raise ValueError("Percentual residual proibido.")
        if not self.service.backend.volume_available(): raise RuntimeError("Controle de volume indisponível.")
    def execute(self, **kwargs: Any) -> str:
        action = kwargs["action"]
        if action == "get":
            percent, muted = self.service.get_volume()
            return f"Volume em {percent} por cento; mudo {'ativo' if muted else 'inativo'}."
        if action in {"increase", "decrease"}: return self.service.change_volume(action)
        if action == "set": return self.service.set_volume(kwargs["percent"])
        return self.service.set_muted(action == "mute")


def local_control_tools(service: LocalControlService) -> list[Tool]:
    tools: list[Tool] = [
        LocalApplicationOpenTool(service), LocalApplicationStatusTool(service),
        LocalApplicationFocusTool(service), LocalApplicationCloseTool(service),
    ]
    if service.backend.volume_available(): tools.append(LocalVolumeTool(service))
    return tools
