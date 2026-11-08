"""Tools de desktop deliberadamente pequenas e sem shell irrestrito."""

from hashlib import sha256
from typing import Any

from jarvis.desktop.models import PreparedDesktopWindow
from jarvis.desktop.service import DesktopService
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


class _PreparedDesktopWindowTool(Tool):
    service: DesktopService

    def prepare(self, **kwargs: Any) -> ToolPreparation:
        prepared = self.service.prepare_window(str(kwargs["title"]))
        binding_digest = sha256(
            prepared.visual_identity().encode("utf-8")
        ).hexdigest()
        return ToolPreparation(
            prepared.public_target,
            prepared,
            binding_digest,
        )

    def get_target(self, **kwargs: Any) -> str:
        return self.prepare(**kwargs).target

    def preflight(self, **kwargs: Any) -> None:
        self.service.prepare_window(str(kwargs["title"]))

    def preflight_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> None:
        self.service.kill_switch.require_available()
        self.service.validate_prepared_window(self._prepared_window(preparation))

    def refresh_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> ToolPreparation:
        refreshed = self.service.refresh_prepared_window(
            self._prepared_window(preparation)
        )
        return ToolPreparation(
            preparation.target,
            refreshed,
            preparation.binding_digest,
        )

    @staticmethod
    def _prepared_window(
        preparation: ToolPreparation,
    ) -> PreparedDesktopWindow:
        context = preparation.context
        if not isinstance(context, PreparedDesktopWindow):
            raise RuntimeError("Contexto preparado da janela ausente.")
        return context


class DesktopListWindowsTool(Tool):
    name = "desktop_list_windows"
    description = (
        "Lista títulos das janelas visíveis atualmente no desktop local."
    )
    risk = RiskLevel.READ_ONLY
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        return "desktop:visible_windows"

    def execute(self, **kwargs: Any) -> str:
        windows = self.service.list_windows()
        if not windows:
            return "Nenhuma janela visível encontrada."
        return "\n".join(f"- {window.title}" for window in windows)


class DesktopListApplicationsTool(Tool):
    name = "desktop_list_applications"
    description = (
        "Lista os aplicativos explicitamente permitidos para abertura pelo Jarvis."
    )
    risk = RiskLevel.READ_ONLY
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def get_target(self, **kwargs: Any) -> str:
        return "desktop:application_catalog"

    def execute(self, **kwargs: Any) -> str:
        applications = self.service.list_applications()
        if not applications:
            return "Nenhum aplicativo permitido está configurado."
        return "Aplicativos permitidos: " + ", ".join(
            application.name for application in applications
        ) + "."


class DesktopFocusWindowTool(_PreparedDesktopWindowTool):
    name = "desktop_focus_window"
    description = (
        "Traz para primeiro plano uma única janela já aberta. Use um trecho "
        "inequívoco do título; se houver ambiguidade, a ação é recusada."
    )
    risk = RiskLevel.EXTERNAL_EFFECT
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Título ou trecho inequívoco da janela aberta.",
            }
        },
        "required": ["title"],
        "additionalProperties": False,
    }

    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        window = self.service.focus_window(str(kwargs["title"]))
        return f'Janela "{window.title}" trazida para primeiro plano.'

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        window = self.service.focus_prepared_window(
            self._prepared_window(preparation)
        )
        return f'Janela "{window.title}" trazida para primeiro plano.'


class DesktopOpenApplicationTool(Tool):
    name = "desktop_open_application"
    description = (
        "Abre um aplicativo somente quando ele pertence ao catálogo local "
        "permitido. Nunca executa comandos, argumentos ou paths arbitrários."
    )
    risk = RiskLevel.EXTERNAL_EFFECT

    def __init__(self, service: DesktopService) -> None:
        self.service = service
        names = [
            application.name
            for application in service.list_applications()
        ]
        self.parameters = {
            "type": "object",
            "properties": {
                "application": {
                    "type": "string",
                    "description": "Nome canônico do aplicativo permitido.",
                    "enum": names,
                }
            },
            "required": ["application"],
            "additionalProperties": False,
        }

    def get_target(self, **kwargs: Any) -> str:
        self.service.kill_switch.require_available()
        application = self.service.resolve_application(
            str(kwargs["application"])
        )
        return f"aplicativo:{application.name}"

    def preflight(self, **kwargs: Any) -> None:
        self.service.kill_switch.require_available()
        self.service.resolve_application(str(kwargs["application"]))

    def execute(self, **kwargs: Any) -> str:
        application = self.service.open_application(
            str(kwargs["application"])
        )
        return (
            f'Solicitação de abertura enviada; aplicativo "{application.name}" '
            "verificado em execução com janela visível."
        )


class DesktopMovePointerToWindowTool(_PreparedDesktopWindowTool):
    name = "desktop_move_pointer_to_window"
    description = (
        "Move o ponteiro somente para o centro de uma janela aberta e "
        "inequívoca. Não aceita coordenadas nem realiza cliques."
    )
    risk = RiskLevel.EXTERNAL_EFFECT
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Título ou trecho inequívoco da janela aberta.",
            }
        },
        "required": ["title"],
        "additionalProperties": False,
    }

    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        window = self.service.move_pointer_to_window_center(
            str(kwargs["title"])
        )
        return (
            f'Ponteiro movido para o centro da janela "{window.title}"; '
            "posição verificada."
        )

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        window = self.service.move_pointer_to_prepared_window_center(
            self._prepared_window(preparation)
        )
        return (
            f'Ponteiro movido para o centro da janela "{window.title}"; '
            "posição verificada."
        )


class DesktopPressEscapeTool(_PreparedDesktopWindowTool):
    name = "desktop_press_escape"
    description = (
        "Envia somente a tecla Escape para uma janela aberta e inequívoca. "
        "Não aceita outras teclas, texto, repetição ou modificadores."
    )
    risk = RiskLevel.EXTERNAL_EFFECT
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Título ou trecho inequívoco da janela aberta.",
            }
        },
        "required": ["title"],
        "additionalProperties": False,
    }

    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        window = self.service.press_escape(str(kwargs["title"]))
        return (
            f'Escape enviado para a janela "{window.title}"; '
            "janela em primeiro plano verificada."
        )

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        window = self.service.press_escape_prepared(
            self._prepared_window(preparation)
        )
        return (
            f'Escape enviado para a janela "{window.title}"; '
            "janela em primeiro plano verificada."
        )


class DesktopCloseWindowTool(_PreparedDesktopWindowTool):
    name = "desktop_close_window"
    description = (
        "Solicita fechamento cooperativo de uma janela aberta e inequívoca. "
        "Não força processo nem responde diálogos de salvamento."
    )
    risk = RiskLevel.SENSITIVE
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Título ou trecho inequívoco da janela aberta.",
            }
        },
        "required": ["title"],
        "additionalProperties": False,
    }

    def __init__(self, service: DesktopService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> str:
        result = self.service.close_window(str(kwargs["title"]))
        return self._format_result(result)

    def execute_prepared(
        self,
        preparation: ToolPreparation,
        **kwargs: Any,
    ) -> str:
        result = self.service.close_prepared_window(
            self._prepared_window(preparation)
        )
        return self._format_result(result)

    @staticmethod
    def _format_result(result) -> str:  # type: ignore[no-untyped-def]
        if result.closed:
            return (
                f'Janela "{result.window.title}" fechada; '
                "ausência verificada."
            )
        return (
            f'Solicitação de fechamento enviada para "{result.window.title}", '
            "mas a janela continua aberta e pode estar aguardando confirmação "
            "de salvamento."
        )


def desktop_tools(service: DesktopService) -> list[Tool]:
    return [
        DesktopListWindowsTool(service),
        DesktopListApplicationsTool(service),
        DesktopFocusWindowTool(service),
        DesktopOpenApplicationTool(service),
        DesktopMovePointerToWindowTool(service),
        DesktopPressEscapeTool(service),
        DesktopCloseWindowTool(service),
    ]
