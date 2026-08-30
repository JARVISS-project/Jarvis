"""Normalização read-only da API Google Calendar."""

from __future__ import annotations

from datetime import date, datetime, time, timezone, tzinfo
from threading import RLock
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.integrations.google.calendar.contracts import CalendarApiClient
from jarvis.integrations.google.calendar.models import (
    CalendarAvailability,
    CalendarEvent,
    CalendarPeriod,
)
from jarvis.integrations.google.errors import (
    GoogleResourceNotFoundError,
    GoogleResponseError,
)


MAX_EVENT_PAGES = 100


class GoogleCalendarProvider:
    def __init__(self, client: CalendarApiClient) -> None:
        self._client = client
        self._timezones: dict[str, tzinfo] = {}
        self._timezone_lock = RLock()

    def get_timezone(self, calendar_id: str) -> tzinfo:
        with self._timezone_lock:
            cached = self._timezones.get(calendar_id)
        if cached is not None:
            return cached
        path = f"/calendars/{quote(calendar_id, safe='')}"
        value = self._client.get(path, {}).get("timeZone")
        if not isinstance(value, str):
            raise GoogleResponseError("Timezone inválido do Google Calendar.")
        try:
            resolved = ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise GoogleResponseError("Timezone desconhecido do Google Calendar.") from None
        with self._timezone_lock:
            self._timezones[calendar_id] = resolved
        return resolved

    def list_events(
        self,
        calendar_id: str,
        period: CalendarPeriod,
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        calendar_timezone = self.get_timezone(calendar_id)
        return self._list_events(
            calendar_id, period.start, period.end, max_results, calendar_timezone
        )

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
    ) -> CalendarEvent | None:
        path = (
            f"/calendars/{quote(calendar_id, safe='')}/events/"
            f"{quote(event_id, safe='')}"
        )
        calendar_timezone = self.get_timezone(calendar_id)
        try:
            data = self._client.get(path, {})
        except GoogleResourceNotFoundError:
            return None
        return self._parse_event(data, calendar_id, calendar_timezone)

    def check_availability(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
    ) -> CalendarAvailability:
        data = self._client.post(
            "/freeBusy",
            {
                "timeMin": start.astimezone(timezone.utc).isoformat(),
                "timeMax": end.astimezone(timezone.utc).isoformat(),
                "items": [{"id": calendar_id}],
            },
        )
        calendars = data.get("calendars")
        if not isinstance(calendars, dict):
            raise GoogleResponseError("Resposta inválida de disponibilidade Calendar.")
        calendar = calendars.get(calendar_id)
        if not isinstance(calendar, dict) or calendar.get("errors"):
            raise GoogleResponseError("Disponibilidade Calendar não pôde ser verificada.")
        busy = calendar.get("busy")
        if not isinstance(busy, list):
            raise GoogleResponseError("Resposta inválida de disponibilidade Calendar.")
        return CalendarAvailability(bool(busy), len(busy))

    def _list_events(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        max_results: int,
        calendar_timezone: tzinfo,
    ) -> list[CalendarEvent]:
        path = f"/calendars/{quote(calendar_id, safe='')}/events"
        events: list[CalendarEvent] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        page_count = 0
        while len(events) < max_results:
            if page_count >= MAX_EVENT_PAGES:
                raise GoogleResponseError(
                    "Paginação do Google Calendar excedeu o limite."
                )
            page_count += 1
            params: dict[str, object] = {
                "timeMin": start.astimezone(timezone.utc).isoformat(),
                "timeMax": end.astimezone(timezone.utc).isoformat(),
                "maxResults": min(50, max_results - len(events)),
                "singleEvents": "true",
                "orderBy": "startTime",
            }
            if page_token is not None:
                params["pageToken"] = page_token
            data = self._client.get(path, params)
            items = data.get("items", [])
            if not isinstance(items, list):
                raise GoogleResponseError("Resposta inválida do Google Calendar.")
            for item in items:
                if not isinstance(item, dict):
                    raise GoogleResponseError("Evento inválido do Google Calendar.")
                events.append(
                    self._parse_event(item, calendar_id, calendar_timezone)
                )
                if len(events) == max_results:
                    break
            token = data.get("nextPageToken")
            if token is None:
                break
            if not isinstance(token, str) or not token:
                raise GoogleResponseError("Paginação inválida do Google Calendar.")
            if token in seen_tokens:
                raise GoogleResponseError("Paginação repetida do Google Calendar.")
            seen_tokens.add(token)
            page_token = token
        return events

    def _parse_event(
        self,
        data: dict[str, Any],
        calendar_id: str,
        calendar_timezone: tzinfo,
    ) -> CalendarEvent:
        event_id = data.get("id")
        start_data = data.get("start")
        end_data = data.get("end")
        if (
            not isinstance(event_id, str)
            or not event_id
            or not isinstance(start_data, dict)
            or not isinstance(end_data, dict)
        ):
            raise GoogleResponseError("Evento inválido do Google Calendar.")
        try:
            if isinstance(start_data.get("date"), str) and isinstance(
                end_data.get("date"), str
            ):
                start = datetime.combine(
                    date.fromisoformat(start_data["date"]),
                    time.min,
                    calendar_timezone,
                )
                end = datetime.combine(
                    date.fromisoformat(end_data["date"]),
                    time.min,
                    calendar_timezone,
                )
                all_day = True
            elif isinstance(start_data.get("dateTime"), str) and isinstance(
                end_data.get("dateTime"), str
            ):
                start = datetime.fromisoformat(start_data["dateTime"])
                end = datetime.fromisoformat(end_data["dateTime"])
                all_day = False
            else:
                raise ValueError
        except ValueError:
            raise GoogleResponseError("Datas inválidas em evento do Calendar.") from None
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise GoogleResponseError("Intervalo inválido em evento do Calendar.")
        summary = data.get("summary", "(sem título)")
        location = data.get("location")
        status = data.get("status", "confirmed")
        if not isinstance(summary, str) or not isinstance(status, str):
            raise GoogleResponseError("Evento inválido do Google Calendar.")
        return CalendarEvent(
            id=event_id,
            summary=summary,
            start=start,
            end=end,
            is_all_day=all_day,
            calendar_id=calendar_id,
            location=location if isinstance(location, str) else None,
            status=status,
            transparent=data.get("transparency") == "transparent",
        )
