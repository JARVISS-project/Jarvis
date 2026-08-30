"""Tools read-only e capability-gated para Google Calendar."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timezone as utc_timezone, tzinfo
from typing import Any

from jarvis.integrations.google.calendar.contracts import CalendarProvider
from jarvis.integrations.google.calendar.models import CalendarPeriod
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


def _validate_calendar_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("calendar_id inválido.")
    return value


class GoogleCalendarListEventsTool(Tool):
    name = "google_calendar_list_events"
    description = "Lista eventos read-only em um período determinístico."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "description": (
                    "today, tomorrow, upcoming, weekday:0-6, next_week ou "
                    "range:YYYY-MM-DD,YYYY-MM-DD"
                ),
            },
            "calendar_id": {"type": "string", "default": "primary"},
            "max_results": {"type": "integer", "default": 20},
        },
        "required": ["period"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        provider: CalendarProvider,
        timezone: tzinfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._timezone = timezone
        self._clock = clock or (lambda: datetime.now().astimezone())

    def get_target(self, **kwargs: Any) -> str:
        return "google_calendar:events"

    def preflight(self, **kwargs: Any) -> None:
        _validate_calendar_id(kwargs.get("calendar_id", "primary"))
        maximum = kwargs.get("max_results", 20)
        if type(maximum) is not int or not 1 <= maximum <= 50:
            raise ValueError("max_results deve estar entre 1 e 50.")
        CalendarPeriod.parse(kwargs["period"], self._timezone, self._clock())

    def execute(self, **kwargs: Any) -> str:
        calendar_id = kwargs.get("calendar_id", "primary")
        timezone = self._provider.get_timezone(calendar_id)
        period = CalendarPeriod.parse(
            kwargs["period"], timezone, self._clock()
        )
        events = self._provider.list_events(
            calendar_id,
            period,
            kwargs.get("max_results", 20),
        )
        if not events:
            return f"Nenhum evento encontrado para {period.label}."
        lines = [f"Eventos para {period.label}:"]
        lines.extend(event.to_display_string(timezone) for event in events)
        return "\n".join(lines)


class GoogleCalendarGetEventTool(Tool):
    name = "google_calendar_get_event"
    description = "Obtém detalhes seguros de um evento read-only pelo ID."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "calendar_id": {"type": "string", "default": "primary"},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    }

    def __init__(self, provider: CalendarProvider, timezone: tzinfo) -> None:
        self._provider = provider
        self._timezone = timezone

    def get_target(self, **kwargs: Any) -> str:
        return "google_calendar:event"

    def preflight(self, **kwargs: Any) -> None:
        _validate_calendar_id(kwargs.get("calendar_id", "primary"))
        event_id = kwargs.get("event_id")
        if not isinstance(event_id, str) or not event_id or len(event_id) > 1024:
            raise ValueError("event_id inválido.")

    def execute(self, **kwargs: Any) -> str:
        calendar_id = kwargs.get("calendar_id", "primary")
        timezone = self._provider.get_timezone(calendar_id)
        event = self._provider.get_event(
            calendar_id, kwargs["event_id"]
        )
        if event is None:
            return "Evento não encontrado."
        lines = [
            f"Evento: {event.summary}",
            f"Horário: {event.to_display_string(timezone)}",
        ]
        if event.location:
            lines.append(f"Local: {event.location}")
        return "\n".join(lines)


class GoogleCalendarCheckAvailabilityTool(Tool):
    name = "google_calendar_check_availability"
    description = "Verifica apenas livre/ocupado em um intervalo read-only."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "description": "today, tomorrow ou weekday:0-6",
            },
            "start_time": {"type": "string", "description": "HH:MM"},
            "end_time": {"type": "string", "description": "HH:MM"},
            "calendar_id": {"type": "string", "default": "primary"},
        },
        "required": ["period", "start_time", "end_time"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        provider: CalendarProvider,
        timezone: tzinfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._timezone = timezone
        self._clock = clock or (lambda: datetime.now().astimezone())

    def get_target(self, **kwargs: Any) -> str:
        return "google_calendar:availability"

    def preflight(self, **kwargs: Any) -> None:
        _validate_calendar_id(kwargs.get("calendar_id", "primary"))
        self._resolve_interval(
            kwargs["period"],
            kwargs["start_time"],
            kwargs["end_time"],
            self._timezone,
        )

    def execute(self, **kwargs: Any) -> str:
        calendar_id = kwargs.get("calendar_id", "primary")
        timezone = self._provider.get_timezone(calendar_id)
        start, end = self._resolve_interval(
            kwargs["period"], kwargs["start_time"], kwargs["end_time"], timezone
        )
        result = self._provider.check_availability(
            calendar_id, start, end
        )
        return result.to_display_string()

    def _resolve_interval(
        self,
        period_value: str,
        start_value: str,
        end_value: str,
        timezone: tzinfo,
    ) -> tuple[datetime, datetime]:
        period = CalendarPeriod.parse(
            period_value, timezone, self._clock()
        )
        if period.kind.value not in {"today", "tomorrow", "weekday"}:
            raise ValueError("Disponibilidade exige um único dia.")
        try:
            start_time = time.fromisoformat(start_value)
            end_time = time.fromisoformat(end_value)
        except (TypeError, ValueError):
            raise ValueError("Horários devem usar HH:MM.") from None
        start = self._resolve_wall_time(period.start.date(), start_time, timezone)
        end = self._resolve_wall_time(period.start.date(), end_time, timezone)
        if end <= start:
            raise ValueError("Horário inicial deve ser anterior ao final.")
        return start, end

    @staticmethod
    def _resolve_wall_time(day, value: time, timezone: tzinfo) -> datetime:
        naive = datetime.combine(day, value)
        candidates: dict[datetime, datetime] = {}
        for fold in (0, 1):
            candidate = naive.replace(tzinfo=timezone, fold=fold)
            round_trip = candidate.astimezone(utc_timezone.utc).astimezone(timezone)
            if round_trip.replace(tzinfo=None) == naive:
                candidates[candidate.astimezone(utc_timezone.utc)] = candidate
        if not candidates:
            raise ValueError("Horário inexistente por mudança de timezone.")
        if len(candidates) > 1:
            raise ValueError("Horário ambíguo por mudança de timezone.")
        return next(iter(candidates.values()))
