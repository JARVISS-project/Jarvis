from jarvis.integrations.google.scopes import (
    AUTHENTICATION_SCOPES,
    CALENDAR_READONLY_SCOPE,
    CALENDAR_SCOPES,
    CALENDAR_WRITE_SCOPE,
    CALENDAR_WRITE_SCOPES,
)
from jarvis.security.capabilities import Capability, TOOL_CAPABILITIES


def test_authentication_requests_only_openid_scope() -> None:
    assert AUTHENTICATION_SCOPES == ("openid",)
    assert CALENDAR_READONLY_SCOPE not in AUTHENTICATION_SCOPES
    assert not any("drive" in scope or "gmail" in scope for scope in AUTHENTICATION_SCOPES)


def test_authentication_does_not_create_operational_capability_or_tool() -> None:
    assert CALENDAR_SCOPES == ("openid", CALENDAR_READONLY_SCOPE)
    assert Capability.GOOGLE_CALENDAR_READ.value == "google_calendar_read"
    assert Capability.GOOGLE_CALENDAR_WRITE.value == "google_calendar_write"
    assert {
        name for name in TOOL_CAPABILITIES if name.startswith("google_calendar_")
    } == {
        "google_calendar_list_events",
        "google_calendar_get_event",
        "google_calendar_check_availability",
        "google_calendar_create_event",
        "google_calendar_update_event",
    }


def test_calendar_write_scope_is_explicit() -> None:
    assert CALENDAR_WRITE_SCOPES == ("openid", CALENDAR_WRITE_SCOPE)
    assert CALENDAR_WRITE_SCOPE == "https://www.googleapis.com/auth/calendar.events"
    assert CALENDAR_READONLY_SCOPE not in CALENDAR_WRITE_SCOPES
