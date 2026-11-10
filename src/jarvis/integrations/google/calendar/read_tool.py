"""Bounded, allowlisted Calendar read through the standard Tool executor."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, tzinfo
import hashlib
import json
import unicodedata
from html.parser import HTMLParser
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
            "output_format": {"type": "string", "enum": ["text", "table"], "default": "text"},
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
            "output_format": kwargs.get("output_format", "text"),
        }, sort_keys=True)
        digest = hashlib.sha256(value.encode()).hexdigest()
        return "google_calendar:events:" + digest

    def preflight(self, **kwargs: Any) -> None:
        if set(kwargs) - {"period", "calendar_id", "max_results", "output_format"}:
            raise ValueError("Argumentos extras não permitidos.")
        if kwargs.get("calendar_id", "primary") not in self.calendar_aliases.values():
            raise PermissionError("Calendário não autorizado.")
        if kwargs.get("period") not in {"today", "tomorrow", "upcoming"}:
            raise ValueError("Use hoje, amanhã ou próximos sete dias.")
        if type(kwargs.get("max_results", 20)) is not int or not 1 <= kwargs.get("max_results", 20) <= 20:
            raise ValueError("Limite de eventos: 1 a 20.")
        if kwargs.get("output_format", "text") not in {"text", "table"}:
            raise ValueError("Formato de agenda inválido.")
        super().preflight(**kwargs)

    def execute(self, **kwargs: Any) -> str:
        self.preflight(**kwargs)
        try:
            if kwargs.get("output_format") == "table":
                return self._table(**kwargs)
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

    def _table(self, **kwargs: Any) -> str:
        """Same bounded read, without Google event or account identifiers."""
        from jarvis.integrations.google.calendar.models import CalendarPeriod

        timezone = self._provider.get_timezone(kwargs["calendar_id"])
        period = CalendarPeriod.parse(kwargs["period"], timezone, self._clock())
        maximum = kwargs.get("max_results", 20)
        events = self._provider.list_events(kwargs["calendar_id"], period, maximum)

        def text(value: str | None, maximum: int) -> str:
            return " ".join("".join(char for char in (value or "")
                if not unicodedata.category(char).startswith("C")).split())[:maximum]

        class PlainNotes(HTMLParser):
            def __init__(self, value):
                super().__init__(convert_charrefs=True)
                self.parts = []
                self.feed((value or '')[:4000])
            def handle_data(self, value):
                self.parts.append(value)

        rows = [{"title": text(event.summary, 160) or "Sem título",
                 'event_key': hashlib.sha256((event.calendar_id + '\0' + event.id).encode()).hexdigest(),
                 'status': event.status,
                 "start": event.start.astimezone(timezone).isoformat(),
                 "end": event.end.astimezone(timezone).isoformat(),
                 "all_day": event.is_all_day, "location": text(event.location, 240),
                 "notes": text(' '.join(PlainNotes(event.description).parts), 500),
                 "transparent": event.transparent,
                 "participants": [{"name": text(person.name, 120), "email": text(person.email, 254),
                    "response": person.response, "self": person.is_self, "resource": person.resource}
                    for person in event.participants[:20]],
                 "participants_omitted": event.participants_omitted}
                for event in events[:maximum] if event.status != "cancelled"]
        rows.sort(key=lambda row: datetime.fromisoformat(row["start"]))
        for row in rows:
            row['fingerprint'] = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return json.dumps({"rows": rows, "timezone": str(timezone),
            "period": kwargs["period"], "limit": maximum,
            "limit_reached": len(rows) == maximum}, ensure_ascii=False)
