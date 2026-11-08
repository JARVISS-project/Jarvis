"""Scopes Google explícitos; novos serviços devem declarar os seus aqui."""

# OIDC is the least-privileged scope that proves user consent. No profile or
# e-mail claim is consumed or persisted in this foundation.
OPENID_SCOPE = "openid"
DRIVE_DOCS_SELECTED_SCOPES = (OPENID_SCOPE, "https://www.googleapis.com/auth/drive.file")
# Native desktop Picker permits drive.file alone (no combined OIDC scope).
DRIVE_PICKER_SCOPES = ("https://www.googleapis.com/auth/drive.file",)
AUTHENTICATION_SCOPES = (OPENID_SCOPE,)

CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_SCOPES = (OPENID_SCOPE, CALENDAR_READONLY_SCOPE)

# Events + metadata used for the calendar timezone. No CalendarList/ACL/write.
CALENDAR_MINIMAL_READ_SCOPES = (
    OPENID_SCOPE,
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.calendars.readonly",
)

CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_WRITE_SCOPES = (OPENID_SCOPE, CALENDAR_WRITE_SCOPE)

# Limited production writes need ACL READ to reject inherited sharing.
# Legacy broad write and the minimal READ grants remain separate and intact.
CALENDAR_LIMITED_WRITE_SCOPES = (
    OPENID_SCOPE,
    "https://www.googleapis.com/auth/calendar.events.owned",
    "https://www.googleapis.com/auth/calendar.acls.readonly",
)
