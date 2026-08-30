from jarvis.integrations.google.scopes import (
    AUTHENTICATION_SCOPES,
    CALENDAR_READONLY_SCOPE,
    CALENDAR_SCOPES,
)
from jarvis.security.capabilities import Capability, TOOL_CAPABILITIES


def test_authentication_requests_only_openid_scope() -> None:
    assert AUTHENTICATION_SCOPES == ("openid",)
    assert CALENDAR_READONLY_SCOPE not in AUTHENTICATION_SCOPES
    assert not any("drive" in scope or "gmail" in scope for scope in AUTHENTICATION_SCOPES)


def test_authentication_does_not_create_operational_capability_or_tool() -> None:
    assert CALENDAR_SCOPES == ("openid", CALENDAR_READONLY_SCOPE)
    assert Capability.GOOGLE_CALENDAR_READ.value == "google_calendar_read"
    assert {
        name for name in TOOL_CAPABILITIES if name.startswith("google_calendar_")
    } == {
        "google_calendar_list_events",
        "google_calendar_get_event",
        "google_calendar_check_availability",
    }
