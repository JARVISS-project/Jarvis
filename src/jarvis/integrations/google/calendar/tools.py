"""Tools read-only e capability-gated para Google Calendar."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timezone as utc_timezone, tzinfo
from typing import Any

from jarvis.integrations.google.calendar.contracts import CalendarProvider, CalendarWriteProvider
from jarvis.integrations.google.calendar.models import CalendarEventInput, CalendarPeriod, CalendarReminder
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


def _validate_calendar_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("calendar_id inválido.")
    return value


def _parse_iso_datetime(value: str, timezone: tzinfo) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Formato de data/hora inválido. Use ISO 8601 com timezone.") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone)
    else:
        dt = dt.astimezone(timezone)
    return dt


def _parse_reminders(value: object, timezone: tzinfo) -> list[CalendarReminder] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("Lembretes devem ser uma lista.")
    reminders = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Cada lembrete deve ser um objeto.")
        method = item.get("method")
        minutes = item.get("minutes")
        if not isinstance(method, str) or not isinstance(minutes, int):
            raise ValueError("Lembrete inválido: method (str) e minutes (int) obrigatórios.")
        reminder = CalendarReminder(method=method, minutes=minutes)
        reminder.validate()
        reminders.append(reminder)
    return reminders


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


class GoogleCalendarCreateEventTool(Tool):
    name = "google_calendar_create_event"
    description = "Cria um evento no Google Calendar com verificação pós-escrita."
    risk = RiskLevel.MEDIUM
    parameters = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Título do evento"},
            "start": {"type": "string", "description": "Início ISO 8601 com timezone (ex: 2026-09-01T14:00:00-03:00)"},
            "end": {"type": "string", "description": "Fim ISO 8601 com timezone (ex: 2026-09-01T15:00:00-03:00)"},
            "is_all_day": {"type": "boolean", "default": False},
            "timezone": {"type": "string", "description": "IANA timezone opcional (ex: America/Sao_Paulo)"},
            "location": {"type": "string", "description": "Local opcional"},
            "description": {"type": "string", "description": "Descrição opcional"},
            "reminders": {
                "type": "array",
                "description": "Lembretes opcionais: [{\"method\": \"popup|email\", \"minutes\": int}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "method": {"type": "string"},
                        "minutes": {"type": "integer"},
                    },
                    "required": ["method", "minutes"],
                },
            },
            "calendar_id": {"type": "string", "default": "primary"},
        },
        "required": ["summary", "start", "end"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        provider: CalendarWriteProvider,
        timezone: tzinfo,
    ) -> None:
        self._provider = provider
        self._timezone = timezone

    def get_target(self, **kwargs: Any) -> str:
        return "google_calendar:event"

    def preflight(self, **kwargs: Any) -> None:
        _validate_calendar_id(kwargs.get("calendar_id", "primary"))
        summary = kwargs.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Título do evento é obrigatório.")
        start = kwargs.get("start")
        end = kwargs.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("Início e fim são obrigatórios em formato ISO 8601.")
        _parse_iso_datetime(start, self._timezone)
        _parse_iso_datetime(end, self._timezone)
        is_all_day = kwargs.get("is_all_day", False)
        if is_all_day:
            # Para all-day, validamos apenas a data
            pass

    def execute(self, **kwargs: Any) -> str:
        calendar_id = kwargs.get("calendar_id", "primary")
        timezone = self._provider.get_timezone(calendar_id)
        start_dt = _parse_iso_datetime(kwargs["start"], timezone)
        end_dt = _parse_iso_datetime(kwargs["end"], timezone)
        if end_dt <= start_dt:
            raise ValueError("Fim deve ser posterior ao início.")

        event_input = CalendarEventInput(
            summary=kwargs["summary"],
            start=start_dt,
            end=end_dt,
            is_all_day=kwargs.get("is_all_day", False),
            timezone=kwargs.get("timezone"),
            location=kwargs.get("location"),
            description=kwargs.get("description"),
            reminders=_parse_reminders(kwargs.get("reminders"), timezone),
        )
        event_input.validate()

        created_event = self._provider.create_event(calendar_id, event_input)

        # Pós-verificação: consultar o evento criado
        verified = self._provider.get_event(calendar_id, created_event.id)
        if verified is None:
            return (
                f"Evento criado (ID: {created_event.id}), mas a verificação "
                f"pós-escrita falhou. Verifique manualmente."
            )

        # Comparar campos essenciais
        if verified.summary != created_event.summary:
            return (
                f"Evento criado (ID: {created_event.id}), mas o título diverge "
                f"após verificação. Esperado: {created_event.summary}, "
                f"Encontrado: {verified.summary}."
            )

        return (
            f"Evento criado e verificado: {verified.to_display_string(timezone)} "
            f"(ID: {verified.id})"
        )


class GoogleCalendarUpdateEventTool(Tool):
    name = "google_calendar_update_event"
    description = "Atualiza um evento existente no Google Calendar com verificação pós-escrita."
    risk = RiskLevel.MEDIUM
    parameters = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "ID do evento a atualizar"},
            "summary": {"type": "string", "description": "Novo título do evento"},
            "start": {"type": "string", "description": "Novo início ISO 8601 com timezone"},
            "end": {"type": "string", "description": "Novo fim ISO 8601 com timezone"},
            "is_all_day": {"type": "boolean", "default": False},
            "timezone": {"type": "string", "description": "IANA timezone opcional"},
            "location": {"type": "string", "description": "Novo local opcional"},
            "description": {"type": "string", "description": "Nova descrição opcional"},
            "reminders": {
                "type": "array",
                "description": "Novos lembretes opcionais: [{\"method\": \"popup|email\", \"minutes\": int}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "method": {"type": "string"},
                        "minutes": {"type": "integer"},
                    },
                    "required": ["method", "minutes"],
                },
            },
            "calendar_id": {"type": "string", "default": "primary"},
        },
        "required": ["event_id", "summary", "start", "end"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        provider: CalendarWriteProvider,
        timezone: tzinfo,
    ) -> None:
        self._provider = provider
        self._timezone = timezone

    def get_target(self, **kwargs: Any) -> str:
        return "google_calendar:event"

    def preflight(self, **kwargs: Any) -> None:
        _validate_calendar_id(kwargs.get("calendar_id", "primary"))
        event_id = kwargs.get("event_id")
        if not isinstance(event_id, str) or not event_id or len(event_id) > 1024:
            raise ValueError("event_id inválido.")
        summary = kwargs.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Título do evento é obrigatório.")
        start = kwargs.get("start")
        end = kwargs.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("Início e fim são obrigatórios em formato ISO 8601.")
        _parse_iso_datetime(start, self._timezone)
        _parse_iso_datetime(end, self._timezone)

    def execute(self, **kwargs: Any) -> str:
        calendar_id = kwargs.get("calendar_id", "primary")
        timezone = self._provider.get_timezone(calendar_id)
        start_dt = _parse_iso_datetime(kwargs["start"], timezone)
        end_dt = _parse_iso_datetime(kwargs["end"], timezone)
        if end_dt <= start_dt:
            raise ValueError("Fim deve ser posterior ao início.")

        event_input = CalendarEventInput(
            summary=kwargs["summary"],
            start=start_dt,
            end=end_dt,
            is_all_day=kwargs.get("is_all_day", False),
            timezone=kwargs.get("timezone"),
            location=kwargs.get("location"),
            description=kwargs.get("description"),
            reminders=_parse_reminders(kwargs.get("reminders"), timezone),
        )
        event_input.validate()

        updated_event = self._provider.update_event(
            calendar_id, kwargs["event_id"], event_input
        )

        # Pós-verificação: consultar o evento atualizado
        verified = self._provider.get_event(calendar_id, updated_event.id)
        if verified is None:
            return (
                f"Evento atualizado (ID: {updated_event.id}), mas a verificação "
                f"pós-escrita falhou. Verifique manualmente."
            )

        # Comparar campos essenciais
        if verified.summary != updated_event.summary:
            return (
                f"Evento atualizado (ID: {updated_event.id}), mas o título diverge "
                f"após verificação. Esperado: {updated_event.summary}, "
                f"Encontrado: {verified.summary}."
            )

        return (
            f"Evento atualizado e verificado: {verified.to_display_string(timezone)} "
            f"(ID: {verified.id})"
        )
