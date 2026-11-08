"""Normalização read-only e write da API Google Calendar."""

from __future__ import annotations

from datetime import date, datetime, time, timezone, tzinfo
import re
from threading import RLock
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.integrations.google.calendar.contracts import (
    CalendarReadApiClient,
    CalendarWriteApiClient,
)
from jarvis.integrations.google.calendar.models import (
    CalendarAvailability,
    CalendarEvent,
    CalendarEventInput,
    CalendarPeriod,
    CalendarReminder,
)
from jarvis.integrations.google.errors import (
    GoogleConflictError,
    GoogleIntegrationError,
    GoogleNetworkError,
    GoogleResourceNotFoundError,
    GoogleResponseError,
    GoogleWriteUnknownError,
)


MAX_EVENT_PAGES = 100


class GoogleCalendarProvider:
    def __init__(self, client: CalendarReadApiClient, *, max_event_pages: int = MAX_EVENT_PAGES) -> None:
        self._client = client
        self._max_event_pages = max_event_pages
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
            if page_count >= self._max_event_pages:
                raise GoogleResponseError(
                    "Paginação do Google Calendar excedeu o limite."
                )
            page_count += 1
            params: dict[str, object] = {
                "timeMin": start.astimezone(timezone.utc).isoformat(),
                "timeMax": end.astimezone(timezone.utc).isoformat(),
                "maxResults": min(50, max_results - len(events)),
                "singleEvents": "true",
                "showDeleted": "false",
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
                if item.get("status") == "cancelled":
                    continue
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


class GoogleCalendarWriteProvider:
    """Provider com autoridade de escrita; storage/credencial separados do read-only."""

    def __init__(self, client: CalendarWriteApiClient) -> None:
        self._client = client

    def private_calendar_version(self, calendar_id: str) -> str:
        """Reject even inherited public/group/domain sharing; never expose ACL values."""
        data = self._client.get(f"/calendars/{quote(calendar_id, safe='')}/acl",
                                {"maxResults": 250, "showDeleted": "false"})
        items, etag = data.get("items"), data.get("etag")
        if (not isinstance(items, list) or not isinstance(etag, str) or not etag
                or data.get("nextPageToken") or not all(isinstance(item, dict) for item in items)):
            raise GoogleResponseError("Compartilhamento do calendário não pôde ser verificado.")
        active = [item for item in items if item.get("role") != "none"]
        if (len(active) != 1 or active[0].get("role") != "owner"
                or not isinstance(active[0].get("scope"), dict)
                or active[0]["scope"].get("type") != "user"):
            raise PermissionError("Calendário compartilhado ou sem proprietário único; escrita bloqueada.")
        return etag

    def overlap_count(self, calendar_id: str, start: datetime, end: datetime,
                      exclude_event_id: str | None = None) -> int:
        data = self._client.get(f"/calendars/{quote(calendar_id, safe='')}/events", {
            "timeMin": start.astimezone(timezone.utc).isoformat(),
            "timeMax": end.astimezone(timezone.utc).isoformat(),
            "singleEvents": "true", "showDeleted": "false", "maxResults": 50,
        })
        items = data.get("items")
        if not isinstance(items, list) or data.get("nextPageToken"):
            raise GoogleResponseError("Conflitos não puderam ser verificados dentro do limite.")
        count = 0
        for item in items:
            if not isinstance(item, dict):
                raise GoogleResponseError("Resposta inválida de conflitos Calendar.")
            if (item.get("id") != exclude_event_id and item.get("status") != "cancelled"
                    and item.get("transparency") != "transparent"):
                count += 1
        return count

    def create_event(
        self,
        calendar_id: str,
        event: CalendarEventInput,
        modified_fields: frozenset[str],
        operation_id: str,
    ) -> CalendarEvent:
        event.validate()
        if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
            raise ValueError("Identidade da operação de criação inválida.")
        event_timezone = self._event_timezone(event)
        body = self._event_to_body(event, event_timezone, modified_fields)
        event_id = operation_id
        body["id"] = event_id
        path = f"/calendars/{quote(calendar_id, safe='')}/events"
        existing = self.get_event(calendar_id, event_id, event_timezone)
        if existing is not None:
            self._require_match(existing, event, modified_fields, event_timezone)
            return existing
        try:
            response = self._client.post(path, body)
        except GoogleConflictError:
            return self._require_verified(
                calendar_id, event_id, event, modified_fields, event_timezone
            )
        except (GoogleNetworkError, GoogleResponseError):
            reconciled = self._reconcile_create(
                calendar_id, event_id, event, modified_fields, event_timezone
            )
            if reconciled is not None:
                return reconciled
            try:
                response = self._client.post(path, body)
            except (GoogleConflictError, GoogleNetworkError, GoogleResponseError):
                return self._require_verified(
                    calendar_id, event_id, event, modified_fields, event_timezone
                )
        response_id = response.get("id")
        if response_id != event_id:
            return self._require_verified(
                calendar_id, event_id, event, modified_fields, event_timezone
            )
        return self._require_verified(
            calendar_id, event_id, event, modified_fields, event_timezone
        )

    def update_event(
        self,
        calendar_id: str,
        event_id: str,
        event: CalendarEventInput,
        modified_fields: frozenset[str],
        *,
        expected_etag: str | None = None,
    ) -> CalendarEvent:
        event.validate()
        event_timezone = self._event_timezone(event)
        original = self.get_event(calendar_id, event_id, event_timezone)
        if original is None:
            raise GoogleResourceNotFoundError(
                "Evento do Google Calendar não encontrado."
            )
        if not original.etag:
            raise GoogleResponseError("Evento sem versão para atualização segura.")
        if expected_etag is not None and original.etag != expected_etag:
            raise GoogleConflictError("Evento alterado depois da prévia; confirme uma nova operação.")
        if expected_etag is not None and not original.simple_owned:
            raise PermissionError("Evento recorrente, compartilhado ou sem identidade de proprietário.")
        if expected_etag is not None and self._matches(original, event, modified_fields, event_timezone):
            return original
        body = self._event_to_body(event, event_timezone, modified_fields)
        if expected_etag is not None:
            body = {key: value for key, value in body.items() if key in modified_fields}
        path = (
            f"/calendars/{quote(calendar_id, safe='')}/events/"
            f"{quote(event_id, safe='')}"
        )
        try:
            response = self._client.patch(path, body, etag=original.etag)
        except GoogleConflictError:
            raise GoogleConflictError(
                "O evento mudou antes da atualização; nada foi sobrescrito."
            ) from None
        except (GoogleNetworkError, GoogleResponseError):
            current = self._reconcile_update(
                calendar_id,
                event_id,
                event,
                modified_fields,
                event_timezone,
                original.etag,
            )
            if current is not None:
                return current
            try:
                response = self._client.patch(path, body, etag=original.etag)
            except GoogleConflictError:
                return self._require_verified(
                    calendar_id, event_id, event, modified_fields, event_timezone
                )
            except (GoogleNetworkError, GoogleResponseError):
                return self._require_verified(
                    calendar_id, event_id, event, modified_fields, event_timezone
                )
        if response.get("id") != event_id:
            return self._require_verified(
                calendar_id, event_id, event, modified_fields, event_timezone
            )
        return self._require_verified(
            calendar_id, event_id, event, modified_fields, event_timezone
        )

    @staticmethod
    def _event_timezone(event: CalendarEventInput) -> tzinfo:
        try:
            return ZoneInfo(event.timezone or "")
        except ZoneInfoNotFoundError:
            raise ValueError("timezone IANA desconhecido.") from None

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
        timezone: tzinfo,
    ) -> CalendarEvent | None:
        path = (
            f"/calendars/{quote(calendar_id, safe='')}/events/"
            f"{quote(event_id, safe='')}"
        )
        try:
            data = self._client.get(path, {})
        except GoogleResourceNotFoundError:
            return None
        return self._parse_event(data, calendar_id, timezone)

    def _event_to_body(
        self,
        event: CalendarEventInput,
        calendar_timezone: tzinfo,
        modified_fields: frozenset[str],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": event.summary.strip(),
        }
        if event.is_all_day:
            body["start"] = {"date": event.start.astimezone(calendar_timezone).date().isoformat()}
            body["end"] = {"date": event.end.astimezone(calendar_timezone).date().isoformat()}
        else:
            body["start"] = {
                "dateTime": event.start.astimezone(calendar_timezone).isoformat(),
                "timeZone": str(calendar_timezone),
            }
            body["end"] = {
                "dateTime": event.end.astimezone(calendar_timezone).isoformat(),
                "timeZone": str(calendar_timezone),
            }
        if "location" in modified_fields:
            body["location"] = event.location
        if "description" in modified_fields:
            body["description"] = event.description
        if "reminders" in modified_fields:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [
                    {"method": r.method, "minutes": r.minutes}
                    for r in event.reminders or ()
                ],
            }
        return body

    def _reconcile_create(
        self,
        calendar_id: str,
        event_id: str,
        event: CalendarEventInput,
        modified_fields: frozenset[str],
        calendar_timezone: tzinfo,
    ) -> CalendarEvent | None:
        try:
            current = self.get_event(calendar_id, event_id, calendar_timezone)
        except (GoogleIntegrationError, PermissionError):
            raise GoogleWriteUnknownError(
                "O resultado da criação é desconhecido; não repita a operação."
            ) from None
        if current is not None:
            self._require_match(current, event, modified_fields, calendar_timezone)
        return current

    def _reconcile_update(
        self,
        calendar_id: str,
        event_id: str,
        event: CalendarEventInput,
        modified_fields: frozenset[str],
        calendar_timezone: tzinfo,
        original_etag: str,
    ) -> CalendarEvent | None:
        try:
            current = self.get_event(calendar_id, event_id, calendar_timezone)
        except (GoogleIntegrationError, PermissionError):
            raise GoogleWriteUnknownError(
                "O resultado da atualização é desconhecido; não repita a operação."
            ) from None
        if current is None:
            raise GoogleWriteUnknownError(
                "O resultado da atualização não pôde ser determinado."
            )
        if self._matches(current, event, modified_fields, calendar_timezone):
            return current
        if current.etag != original_etag:
            raise GoogleWriteUnknownError(
                "A atualização pode ter ocorrido antes de outra alteração; estado desconhecido."
            )
        return None

    def _require_verified(
        self,
        calendar_id: str,
        event_id: str,
        event: CalendarEventInput,
        modified_fields: frozenset[str],
        calendar_timezone: tzinfo,
    ) -> CalendarEvent:
        try:
            verified = self.get_event(calendar_id, event_id, calendar_timezone)
        except (GoogleIntegrationError, PermissionError):
            raise GoogleWriteUnknownError(
                "O resultado da escrita é desconhecido porque a verificação final falhou."
            ) from None
        if verified is None:
            raise GoogleWriteUnknownError(
                "A escrita não pôde ser confirmada pela verificação final."
            )
        if verified.id != event_id:
            raise GoogleWriteUnknownError(
                "A verificação retornou identidade diferente da escrita solicitada."
            )
        self._require_match(verified, event, modified_fields, calendar_timezone)
        return verified

    def _require_match(
        self,
        current: CalendarEvent,
        event: CalendarEventInput,
        modified_fields: frozenset[str],
        calendar_timezone: tzinfo,
    ) -> None:
        if not self._matches(current, event, modified_fields, calendar_timezone):
            raise GoogleWriteUnknownError(
                "A escrita divergiu dos campos solicitados na verificação final."
            )

    @staticmethod
    def _matches(
        current: CalendarEvent,
        event: CalendarEventInput,
        modified_fields: frozenset[str],
        calendar_timezone: tzinfo,
    ) -> bool:
        if current.summary != event.summary.strip() or current.is_all_day != event.is_all_day:
            return False
        if event.is_all_day:
            expected_start = event.start.astimezone(calendar_timezone).date()
            expected_end = event.end.astimezone(calendar_timezone).date()
            if current.start.date() != expected_start or current.end.date() != expected_end:
                return False
        elif (
            current.start.astimezone(timezone.utc) != event.start.astimezone(timezone.utc)
            or current.end.astimezone(timezone.utc) != event.end.astimezone(timezone.utc)
        ):
            return False
        if (
            not event.is_all_day
            and "timezone" in modified_fields
            and current.timezone_name != event.timezone
        ):
            return False
        if "location" in modified_fields and current.location != event.location:
            return False
        if "description" in modified_fields and current.description != event.description:
            return False
        if "reminders" in modified_fields:
            if current.reminders_use_default is not False:
                return False
            actual = sorted((r.method, r.minutes) for r in current.reminders or ())
            expected = sorted((r.method, r.minutes) for r in event.reminders or ())
            if actual != expected:
                return False
        return True

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
        description = data.get("description")
        status = data.get("status", "confirmed")
        if not isinstance(summary, str) or not isinstance(status, str):
            raise GoogleResponseError("Evento inválido do Google Calendar.")
        reminders_data = data.get("reminders")
        reminders: tuple[CalendarReminder, ...] | None = None
        reminders_use_default: bool | None = None
        if isinstance(reminders_data, dict):
            use_default = reminders_data.get("useDefault")
            if type(use_default) is not bool:
                raise GoogleResponseError("Lembretes inválidos do Google Calendar.")
            reminders_use_default = use_default
            overrides = reminders_data.get("overrides", [])
            if not isinstance(overrides, list):
                raise GoogleResponseError("Lembretes inválidos do Google Calendar.")
            parsed_reminders: list[CalendarReminder] = []
            for item in overrides:
                if not isinstance(item, dict):
                    raise GoogleResponseError("Lembrete inválido do Google Calendar.")
                method = item.get("method")
                minutes = item.get("minutes")
                if not isinstance(method, str) or type(minutes) is not int:
                    raise GoogleResponseError("Lembrete inválido do Google Calendar.")
                reminder = CalendarReminder(method, minutes)
                try:
                    reminder.validate()
                except ValueError:
                    raise GoogleResponseError(
                        "Lembrete inválido do Google Calendar."
                    ) from None
                parsed_reminders.append(reminder)
            reminders = tuple(parsed_reminders)
        etag = data.get("etag")
        if not isinstance(etag, str) or not etag:
            raise GoogleResponseError("Evento sem versão do Google Calendar.")
        timezone_name = start_data.get("timeZone")
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
            timezone_name=timezone_name if isinstance(timezone_name, str) else None,
            description=description if isinstance(description, str) else None,
            reminders=reminders,
            reminders_use_default=reminders_use_default,
            etag=etag,
            simple_owned=(
                isinstance(data.get("organizer"), dict)
                and data["organizer"].get("self") is True
                and not data.get("recurrence") and not data.get("recurringEventId")
                and not data.get("attendees") and not data.get("attendeesOmitted")
                and not data.get("attachments")
                and data.get("visibility") != "public"
                and not data.get("extendedProperties", {}).get("shared")
                and data.get("eventType", "default") == "default"
                and status == "confirmed"
            ),
        )
