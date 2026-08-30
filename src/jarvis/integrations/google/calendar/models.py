"""Modelos normalizados e períodos determinísticos do Google Calendar."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from enum import Enum


class CalendarPeriodKind(str, Enum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    UPCOMING = "upcoming"
    WEEKDAY = "weekday"
    NEXT_WEEK = "next_week"
    DATE_RANGE = "date_range"


@dataclass(frozen=True, slots=True)
class CalendarPeriod:
    kind: CalendarPeriodKind
    start: datetime = field(repr=False)
    end: datetime = field(repr=False)
    label: str
    weekday_name: str | None = None

    @classmethod
    def parse(
        cls,
        value: str,
        timezone: tzinfo,
        now: datetime,
    ) -> CalendarPeriod:
        if now.tzinfo is None:
            raise ValueError("O relógio do Calendar deve possuir timezone.")
        local_now = now.astimezone(timezone)
        today = local_now.date()
        if value == "today":
            return cls._from_dates(
                CalendarPeriodKind.TODAY,
                today,
                today + timedelta(days=1),
                "hoje",
                timezone,
            )
        if value == "tomorrow":
            start = today + timedelta(days=1)
            return cls._from_dates(
                CalendarPeriodKind.TOMORROW,
                start,
                start + timedelta(days=1),
                "amanhã",
                timezone,
            )
        if value == "upcoming":
            return cls(
                kind=CalendarPeriodKind.UPCOMING,
                start=local_now,
                end=local_now + timedelta(days=7),
                label="próximos 7 dias",
            )
        if value == "next_week":
            start = today + timedelta(days=7 - today.weekday())
            return cls._from_dates(
                CalendarPeriodKind.NEXT_WEEK,
                start,
                start + timedelta(days=7),
                "próxima semana",
                timezone,
            )
        if value.startswith("weekday:"):
            try:
                weekday = int(value.removeprefix("weekday:"))
            except ValueError:
                weekday = -1
            if not 0 <= weekday <= 6:
                raise ValueError("weekday deve estar entre 0 e 6.")
            days_ahead = (weekday - today.weekday()) % 7 or 7
            start = today + timedelta(days=days_ahead)
            names = (
                "segunda-feira",
                "terça-feira",
                "quarta-feira",
                "quinta-feira",
                "sexta-feira",
                "sábado",
                "domingo",
            )
            return cls._from_dates(
                CalendarPeriodKind.WEEKDAY,
                start,
                start + timedelta(days=1),
                names[weekday],
                timezone,
                weekday_name=names[weekday],
            )
        if value.startswith("range:"):
            try:
                start_text, end_text = value.removeprefix("range:").split(",", 1)
                start = date.fromisoformat(start_text)
                inclusive_end = date.fromisoformat(end_text)
            except ValueError:
                raise ValueError(
                    "range deve usar range:YYYY-MM-DD,YYYY-MM-DD."
                ) from None
            if inclusive_end < start or (inclusive_end - start).days > 366:
                raise ValueError("Intervalo Calendar inválido ou maior que 366 dias.")
            return cls._from_dates(
                CalendarPeriodKind.DATE_RANGE,
                start,
                inclusive_end + timedelta(days=1),
                f"{start:%d/%m/%Y} a {inclusive_end:%d/%m/%Y}",
                timezone,
            )
        raise ValueError("Período Calendar inválido.")

    @classmethod
    def _from_dates(
        cls,
        kind: CalendarPeriodKind,
        start: date,
        end: date,
        label: str,
        timezone: tzinfo,
        *,
        weekday_name: str | None = None,
    ) -> CalendarPeriod:
        return cls(
            kind=kind,
            start=datetime.combine(start, time.min, timezone),
            end=datetime.combine(end, time.min, timezone),
            label=label,
            weekday_name=weekday_name,
        )


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    id: str = field(repr=False)
    summary: str = field(repr=False)
    start: datetime
    end: datetime
    is_all_day: bool
    calendar_id: str = field(repr=False)
    location: str | None = field(default=None, repr=False)
    status: str = "confirmed"
    transparent: bool = False
    timezone_name: str | None = None
    description: str | None = field(default=None, repr=False)
    reminders: tuple[CalendarReminder, ...] | None = field(default=None, repr=False)
    reminders_use_default: bool | None = field(default=None, repr=False)
    etag: str | None = field(default=None, repr=False)

    def to_display_string(self, timezone: tzinfo) -> str:
        local_start = self.start.astimezone(timezone)
        local_end = self.end.astimezone(timezone)
        if self.is_all_day:
            return f"{local_start:%d/%m/%Y}, dia inteiro: {self.summary}"
        return (
            f"{local_start:%d/%m/%Y %H:%M}-{local_end:%H:%M}: "
            f"{self.summary}"
        )


@dataclass(frozen=True, slots=True)
class CalendarEventInput:
    """Entrada tipada para criação/atualização de eventos."""
    summary: str
    start: datetime
    end: datetime
    is_all_day: bool = False
    timezone: str | None = None
    location: str | None = None
    description: str | None = None
    reminders: tuple[CalendarReminder, ...] | None = None

    def validate(self) -> None:
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("Título do evento é obrigatório.")
        if len(self.summary) > 1024:
            raise ValueError("Título do evento excede o limite.")
        if type(self.is_all_day) is not bool:
            raise ValueError("is_all_day deve ser booleano.")
        if not isinstance(self.timezone, str) or not self.timezone:
            raise ValueError("timezone IANA é obrigatório.")
        if self.location is not None and not isinstance(self.location, str):
            raise ValueError("Local do evento deve ser texto.")
        if self.location is not None and len(self.location) > 4096:
            raise ValueError("Local do evento excede o limite.")
        if self.description is not None and not isinstance(self.description, str):
            raise ValueError("Descrição do evento deve ser texto.")
        if self.description is not None and len(self.description) > 32768:
            raise ValueError("Descrição do evento excede o limite.")
        if type(self.start) is not datetime or type(self.end) is not datetime:
            raise ValueError("Início e fim devem ser datas válidas.")
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Início e fim devem possuir timezone.")
        if self.end <= self.start:
            raise ValueError("Fim deve ser posterior ao início.")
        if self.reminders is not None and type(self.reminders) is not tuple:
            raise ValueError("Lembretes devem ser uma tupla validada.")
        if self.reminders is not None and len(self.reminders) > 5:
            raise ValueError("No máximo 5 lembretes são permitidos.")
        if self.reminders:
            for reminder in self.reminders:
                if not isinstance(reminder, CalendarReminder):
                    raise ValueError("Lembrete inválido.")
                reminder.validate()


@dataclass(frozen=True, slots=True)
class CalendarReminder:
    method: str  # "popup" ou "email"
    minutes: int

    def validate(self) -> None:
        if self.method not in ("popup", "email"):
            raise ValueError("Método de lembrete deve ser 'popup' ou 'email'.")
        if type(self.minutes) is not int or not 0 <= self.minutes <= 40320:
            raise ValueError("Minutos do lembrete devem estar entre 0 e 40320.")


@dataclass(frozen=True, slots=True)
class CalendarAvailability:
    busy: bool
    event_count: int

    def to_display_string(self) -> str:
        if not self.busy:
            return "Livre no período consultado."
        suffix = "intervalo ocupado" if self.event_count == 1 else "intervalos ocupados"
        return f"Ocupado ({self.event_count} {suffix} no período)."
