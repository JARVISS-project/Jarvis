from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import tomllib
from typing import Any
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

from jarvis.integrations.google.calendar import client as calendar_client_module
from jarvis.integrations.google.calendar.client import GoogleCalendarApiClient
from jarvis.integrations.google.calendar.google_provider import (
    MAX_EVENT_PAGES,
    GoogleCalendarProvider,
)
from jarvis.integrations.google.calendar.models import (
    CalendarAvailability,
    CalendarEvent,
    CalendarPeriod,
    CalendarPeriodKind,
)
from jarvis.integrations.google.calendar.tools import (
    GoogleCalendarCheckAvailabilityTool,
    GoogleCalendarGetEventTool,
    GoogleCalendarListEventsTool,
)
from jarvis.integrations.google.errors import (
    GoogleNetworkError,
    GooglePermissionError,
    GoogleRateLimitError,
    GoogleResourceNotFoundError,
    GoogleResponseError,
)
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor


FIXED_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def event_payload(event_id: str = "event-1") -> dict[str, Any]:
    return {
        "id": event_id,
        "summary": "Reunião privada",
        "start": {"dateTime": "2026-08-29T14:00:00-03:00"},
        "end": {"dateTime": "2026-08-29T15:00:00-03:00"},
        "status": "confirmed",
    }


class FakeApiClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, path: str, params: dict[str, object]) -> dict[str, Any]:
        self.calls.append((path, dict(params)))
        return self._next()

    def post(self, path: str, body: dict[str, object]) -> dict[str, Any]:
        self.calls.append((path, dict(body)))
        return self._next()

    def _next(self) -> dict[str, Any]:
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeProvider:
    def __init__(
        self,
        events: list[CalendarEvent] | None = None,
        calendar_timezone=timezone.utc,
    ) -> None:
        self.events = events or []
        self.calls = 0
        self.calendar_timezone = calendar_timezone

    def get_timezone(self, _calendar_id):
        return self.calendar_timezone

    def list_events(self, _calendar_id, _period, max_results=50):
        self.calls += 1
        return self.events[:max_results]

    def get_event(self, _calendar_id, event_id):
        self.calls += 1
        return next((event for event in self.events if event.id == event_id), None)

    def check_availability(self, _calendar_id, _start, _end):
        self.calls += 1
        return CalendarAvailability(bool(self.events), len(self.events))


class FakeCredential:
    def __init__(self) -> None:
        self.valid = True
        self.access_token = "access-token-secret"
        self.refresh_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1


class FakeAuth:
    def __init__(self, credential: FakeCredential) -> None:
        self.credential = credential
        self.calls = 0

    def credentials(self) -> FakeCredential:
        self.calls += 1
        return self.credential

    def refresh_credentials(self) -> FakeCredential:
        self.credential.refresh()
        return self.credential


class FakeHttpResponse:
    def __init__(self, value: object) -> None:
        self._raw = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit: int) -> bytes:
        return self._raw[:limit]


def test_period_resolution_uses_injected_clock_and_dst_boundaries() -> None:
    tz = ZoneInfo("America/New_York")
    before_dst = datetime(2026, 3, 7, 12, 0, tzinfo=tz)

    period = CalendarPeriod.parse("tomorrow", tz, before_dst)

    assert period.kind is CalendarPeriodKind.TOMORROW
    assert period.start.isoformat() == "2026-03-08T00:00:00-05:00"
    assert period.end.isoformat() == "2026-03-09T00:00:00-04:00"


def test_google_extra_declares_timezone_database_for_clean_windows() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["optional-dependencies"]["google"]

    assert any(dependency.startswith("tzdata") for dependency in dependencies)


def test_period_range_is_inclusive_and_bounded() -> None:
    period = CalendarPeriod.parse(
        "range:2026-09-01,2026-09-05", timezone.utc, FIXED_NOW
    )

    assert period.start.isoformat() == "2026-09-01T00:00:00+00:00"
    assert period.end.isoformat() == "2026-09-06T00:00:00+00:00"
    with pytest.raises(ValueError, match="366"):
        CalendarPeriod.parse(
            "range:2026-01-01,2028-01-01", timezone.utc, FIXED_NOW
        )


def test_upcoming_period_starts_at_current_time() -> None:
    period = CalendarPeriod.parse("upcoming", timezone.utc, FIXED_NOW)

    assert period.start == FIXED_NOW
    assert period.end == datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def test_provider_paginates_and_encodes_calendar_id() -> None:
    client = FakeApiClient(
        [
            {"timeZone": "UTC"},
            {"items": [event_payload("one")], "nextPageToken": "next"},
            {"items": [event_payload("two")]},
        ]
    )
    provider = GoogleCalendarProvider(client)
    period = CalendarPeriod.parse("today", timezone.utc, FIXED_NOW)

    events = provider.list_events("team/calendar", period, max_results=2)

    assert [event.id for event in events] == ["one", "two"]
    assert client.calls[1][0] == "/calendars/team%2Fcalendar/events"
    assert client.calls[2][1]["pageToken"] == "next"


def test_provider_rejects_repeated_page_token() -> None:
    client = FakeApiClient([
        {"timeZone": "UTC"},
        {"items": [], "nextPageToken": "same"},
        {"items": [], "nextPageToken": "same"},
    ])
    provider = GoogleCalendarProvider(client)

    with pytest.raises(GoogleResponseError, match="repetida"):
        provider.list_events(
            "primary",
            CalendarPeriod.parse("today", timezone.utc, FIXED_NOW),
        )


def test_provider_bounds_empty_pages_with_unique_tokens() -> None:
    responses: list[object] = [{"timeZone": "UTC"}]
    responses.extend(
        {"items": [], "nextPageToken": f"token-{index}"}
        for index in range(MAX_EVENT_PAGES)
    )
    client = FakeApiClient(responses)
    provider = GoogleCalendarProvider(client)

    with pytest.raises(GoogleResponseError, match="excedeu o limite"):
        provider.list_events(
            "primary",
            CalendarPeriod.parse("today", timezone.utc, FIXED_NOW),
        )

    assert len(client.calls) == MAX_EVENT_PAGES + 1


def test_get_event_returns_none_only_for_not_found() -> None:
    missing = GoogleCalendarProvider(FakeApiClient([
        {"timeZone": "UTC"}, GoogleResourceNotFoundError("missing")
    ]))
    failing = GoogleCalendarProvider(FakeApiClient([
        {"timeZone": "UTC"}, GoogleNetworkError("offline")
    ]))

    assert missing.get_event("primary", "id/with slash") is None
    with pytest.raises(GoogleNetworkError, match="offline"):
        failing.get_event("primary", "event")


def test_provider_rejects_malformed_event() -> None:
    provider = GoogleCalendarProvider(FakeApiClient([
        {"timeZone": "UTC"}, {"id": "broken"}
    ]))

    with pytest.raises(GoogleResponseError, match="Evento inválido"):
        provider.get_event("primary", "broken")


def test_all_day_event_uses_calendar_timezone() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    client = FakeApiClient(
        [{"timeZone": "America/Sao_Paulo"}, {
            "id": "holiday",
            "summary": "Feriado",
            "start": {"date": "2026-09-07"},
            "end": {"date": "2026-09-08"},
        }]
    )

    event = GoogleCalendarProvider(client).get_event("primary", "holiday")

    assert event is not None
    assert event.start.isoformat() == "2026-09-07T00:00:00-03:00"


def test_availability_uses_freebusy_without_event_content() -> None:
    client = FakeApiClient([{
        "calendars": {"primary": {"busy": []}}
    }])
    provider = GoogleCalendarProvider(client)

    result = provider.check_availability(
        "primary",
        datetime(2026, 8, 29, tzinfo=timezone.utc),
        datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert result.to_display_string() == "Livre no período consultado."
    assert client.calls[0][0] == "/freeBusy"
    assert "items" in client.calls[0][1]


def test_list_tool_uses_fixed_clock() -> None:
    provider = FakeProvider()
    tool = GoogleCalendarListEventsTool(
        provider, timezone.utc, clock=lambda: FIXED_NOW
    )

    assert tool.execute(period="today") == "Nenhum evento encontrado para hoje."
    with pytest.raises(ValueError, match="max_results"):
        tool.preflight(period="today", max_results=51)


def test_get_event_tool_returns_minimized_details() -> None:
    event = CalendarEvent(
        id="secret-event-id",
        summary="Consulta",
        start=datetime(2026, 8, 29, 14, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, 15, tzinfo=timezone.utc),
        is_all_day=False,
        calendar_id="private-calendar-id",
        location="Clínica",
    )

    result = GoogleCalendarGetEventTool(
        FakeProvider([event]), timezone.utc
    ).execute(event_id=event.id)

    assert "Consulta" in result
    assert "Clínica" in result
    assert "secret-event-id" not in result
    assert "private-calendar-id" not in result


def test_availability_tool_validates_interval_before_provider() -> None:
    provider = FakeProvider()
    tool = GoogleCalendarCheckAvailabilityTool(
        provider, timezone.utc, clock=lambda: FIXED_NOW
    )

    with pytest.raises(ValueError, match="anterior"):
        tool.preflight(
            period="today",
            start_time="11:00",
            end_time="10:00",
        )
    assert provider.calls == 0


def test_availability_rejects_nonexistent_and_ambiguous_wall_times() -> None:
    tz = ZoneInfo("America/New_York")
    spring = GoogleCalendarCheckAvailabilityTool(
        FakeProvider(calendar_timezone=tz),
        tz,
        clock=lambda: datetime(2026, 3, 8, 0, 30, tzinfo=tz),
    )
    with pytest.raises(ValueError, match="inexistente"):
        spring.preflight(
            period="today", start_time="02:30", end_time="03:30"
        )

    fall = GoogleCalendarCheckAvailabilityTool(
        FakeProvider(calendar_timezone=tz),
        tz,
        clock=lambda: datetime(2026, 11, 1, 0, 30, tzinfo=tz),
    )
    with pytest.raises(ValueError, match="ambíguo"):
        fall.preflight(
            period="today", start_time="01:30", end_time="02:30"
        )


def test_disabled_capability_blocks_provider_and_audit_target_is_private(
    tmp_path: Path,
) -> None:
    provider = FakeProvider()
    tool = GoogleCalendarGetEventTool(provider, timezone.utc)
    audit_path = tmp_path / "audit.log"
    disabled = ToolExecutor(
        SafetyManager(),
        AuditLogger(audit_path),
        CapabilityPolicy({Capability.GOOGLE_CALENDAR_READ: False}),
    )

    result = disabled.execute(tool, event_id="sensitive-event-id")

    assert "capacidade desabilitada" in result.casefold()
    assert provider.calls == 0
    audit = audit_path.read_text(encoding="utf-8")
    assert "target=desconhecido" in audit
    assert "sensitive-event-id" not in audit

    enabled_path = tmp_path / "enabled-audit.log"
    enabled = ToolExecutor(
        SafetyManager(),
        AuditLogger(enabled_path),
        CapabilityPolicy({Capability.GOOGLE_CALENDAR_READ: True}),
    )
    enabled.execute(tool, event_id="another-sensitive-id")
    enabled_audit = enabled_path.read_text(encoding="utf-8")
    assert "target=google_calendar:event" in enabled_audit
    assert "another-sensitive-id" not in enabled_audit


def test_api_client_gets_credentials_lazily_and_sends_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    auth = FakeAuth(credential)
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeHttpResponse({"items": []})

    monkeypatch.setattr(calendar_client_module, "urlopen", fake_urlopen)

    result = GoogleCalendarApiClient(auth).get("/calendars/primary/events", {})

    assert result == {"items": []}
    assert auth.calls == 1
    assert requests[0][0].get_header("Authorization") == (
        "Bearer access-token-secret"
    )


def test_api_client_refreshes_once_after_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = FakeCredential()
    responses = [
        HTTPError("url", 401, "unauthorized", {}, None),
        FakeHttpResponse({"items": []}),
    ]

    def fake_urlopen(_request, timeout):
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(calendar_client_module, "urlopen", fake_urlopen)

    GoogleCalendarApiClient(FakeAuth(credential)).get("/calendar", {})

    assert credential.refresh_calls == 1


def test_api_client_maps_rate_limit_without_response_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(_request, timeout):
        raise HTTPError("url", 429, "secret response", {}, None)

    monkeypatch.setattr(calendar_client_module, "urlopen", fake_urlopen)

    with pytest.raises(GoogleRateLimitError) as error:
        GoogleCalendarApiClient(FakeAuth(FakeCredential())).get("/calendar", {})

    assert "secret response" not in str(error.value)


def test_api_client_maps_permission_failure_without_response_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(_request, timeout):
        raise HTTPError("url", 403, "secret response", {}, None)

    monkeypatch.setattr(calendar_client_module, "urlopen", fake_urlopen)

    with pytest.raises(GooglePermissionError) as error:
        GoogleCalendarApiClient(FakeAuth(FakeCredential())).get("/calendar", {})

    assert "não está habilitada ou permitida" in str(error.value)
    assert "secret response" not in str(error.value)


@pytest.mark.parametrize(
    "reason",
    ("rateLimitExceeded", "userRateLimitExceeded"),
)
def test_api_client_maps_google_403_rate_limit_reasons(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    body = json.dumps(
        {"error": {"errors": [{"reason": reason, "message": "private"}]}}
    ).encode("utf-8")

    def fake_urlopen(_request, timeout):
        raise HTTPError("url", 403, "private response", {}, BytesIO(body))

    monkeypatch.setattr(calendar_client_module, "urlopen", fake_urlopen)

    with pytest.raises(GoogleRateLimitError) as error:
        GoogleCalendarApiClient(FakeAuth(FakeCredential())).get("/calendar", {})

    assert "Limite temporário" in str(error.value)
    assert "private" not in str(error.value)
