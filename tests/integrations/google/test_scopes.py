from jarvis.integrations.google.scopes import (
    AUTHENTICATION_SCOPES,
    CALENDAR_READONLY_SCOPE,
)
from jarvis.security.capabilities import Capability, TOOL_CAPABILITIES


def test_authentication_requests_only_openid_scope() -> None:
    assert AUTHENTICATION_SCOPES == ("openid",)
    assert CALENDAR_READONLY_SCOPE not in AUTHENTICATION_SCOPES
    assert not any("drive" in scope or "gmail" in scope for scope in AUTHENTICATION_SCOPES)


def test_authentication_does_not_create_operational_capability_or_tool() -> None:
    assert "GOOGLE" not in Capability.__members__
    assert not any(name.startswith("google_") for name in TOOL_CAPABILITIES)
