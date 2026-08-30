"""Contratos testáveis da integração read-only com Calendar."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any, Protocol

from jarvis.integrations.google.calendar.models import (
    CalendarAvailability,
    CalendarEvent,
    CalendarPeriod,
)


class CalendarApiClient(Protocol):
    def get(self, path: str, params: dict[str, object]) -> dict[str, Any]: ...
    def post(self, path: str, body: dict[str, object]) -> dict[str, Any]: ...


class CalendarProvider(Protocol):
    def get_timezone(self, calendar_id: str) -> tzinfo: ...

    def list_events(
        self,
        calendar_id: str,
        period: CalendarPeriod,
        max_results: int = 50,
    ) -> list[CalendarEvent]: ...

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
    ) -> CalendarEvent | None: ...

    def check_availability(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
    ) -> CalendarAvailability: ...
