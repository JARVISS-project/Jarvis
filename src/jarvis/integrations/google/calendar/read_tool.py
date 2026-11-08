"""Bounded, allowlisted Calendar read through the standard Tool executor."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, tzinfo
import hashlib
import json
from types import MappingProxyType
from typing import Any

from jarvis.integrations.google.calendar.contracts import CalendarProvider
from jarvis.integrations.google.calendar.tools import GoogleCalendarListEventsTool
from jarvis.security.risk import RiskLevel
from jarvis.integrations.google.errors import (
    GoogleIntegrationError, GoogleNetworkError, GooglePermissionError,
    GoogleRateLimitError, GoogleScopeError, GoogleTokenRevokedError,
)


class GoogleCalendarReadAgendaTool(GoogleCalendarListEventsTool):
    """Reuse the existing name/capability while restricting the production read."""

    name = "google_calendar_list_events"
    risk = RiskLevel.READ_ONLY

    parameters = {
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["today", "tomorrow", "upcoming"]},
            "calendar_id": {"type": "string"},
            "max_results": {"type": "integer", "default": 20},
        },
        "required": ["period", "calendar_id"],
        "additionalProperties": False,
    }

    def __init__(
        self, provider: CalendarProvider, timezone: tzinfo,
        calendars: dict[str, str], clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(provider, timezone, clock)
        self.calendar_aliases = MappingProxyType(dict(calendars))

    def resolve_calendar(self, alias: str) -> str:
        value = self.calendar_aliases.get(alias)
        if value is None:
            raise PermissionError("Calendário não autorizado; configure um alias explícito.")
        return value

    def get_target(self, **kwargs: Any) -> str:
        # Bind the target without putting a calendar/account ID in audit logs.
        value = json.dumps({
            "calendar_id": kwargs.get("calendar_id"),
            "period": kwargs.get("period"),
            "max_results": kwargs.get("max_results", 20),
        }, sort_keys=True)
        digest = hashlib.sha256(value.encode()).hexdigest()
        return "google_calendar:events:" + digest

    def preflight(self, **kwargs: Any) -> None:
        if set(kwargs) - {"period", "calendar_id", "max_results"}:
            raise ValueError("Argumentos extras não permitidos.")
        if kwargs.get("calendar_id", "primary") not in self.calendar_aliases.values():
            raise PermissionError("Calendário não autorizado.")
        if kwargs.get("period") not in {"today", "tomorrow", "upcoming"}:
            raise ValueError("Use hoje, amanhã ou próximos sete dias.")
        if type(kwargs.get("max_results", 20)) is not int or not 1 <= kwargs.get("max_results", 20) <= 20:
            raise ValueError("Limite de eventos: 1 a 20.")
        super().preflight(**kwargs)

    def execute(self, **kwargs: Any) -> str:
        self.preflight(**kwargs)
        try:
            result = super().execute(**kwargs)
        except (GoogleScopeError, GoogleTokenRevokedError):
            raise RuntimeError("Google Calendar indisponível: autorização ou escopo ausente; reconecte explicitamente.") from None
        except GoogleRateLimitError:
            raise RuntimeError("Google Calendar indisponível temporariamente: limite de consultas; não houve retry automático.") from None
        except GooglePermissionError:
            raise RuntimeError("Google Calendar indisponível: confira acesso ao calendário e habilitação da API (403).") from None
        except GoogleNetworkError:
            raise RuntimeError("Google Calendar indisponível: rede ou timeout; nenhum compromisso confirmado.") from None
        except GoogleIntegrationError:
            raise RuntimeError("Google Calendar indisponível: resposta ou dependência inválida; confira --google-calendar status.") from None
        except Exception:
            raise RuntimeError("Consulta Google indisponível; nenhum evento confirmado.") from None
        if len(result) > 4000:
            return result[:3950] + "\nResposta limitada a 4000 caracteres."
        return result
