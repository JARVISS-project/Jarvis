"""Composição local da fundação Google."""

from pathlib import Path

from jarvis.integrations.google.auth import GoogleAuthManager
from jarvis.integrations.google.client import (
    GoogleOAuthFlowClient,
    load_google_credentials,
)
from jarvis.integrations.google.loopback import LoopbackOAuthReceiver
from jarvis.integrations.google.operation_lock import GoogleOperationLock
from jarvis.integrations.google.scopes import AUTHENTICATION_SCOPES, CALENDAR_SCOPES, CALENDAR_WRITE_SCOPES
from jarvis.integrations.google.storage import (
    ProtectedFileCredentialStore,
    WindowsDPAPIProtector,
)


def build_google_auth_manager(
    credential_path: Path = Path("data/google/credentials.bin"),
    scopes: tuple[str, ...] = AUTHENTICATION_SCOPES,
    upgrade_from_scopes: tuple[str, ...] = (),
    allowed_scopes: tuple[str, ...] | None = None,
) -> GoogleAuthManager:
    store = ProtectedFileCredentialStore(
        credential_path,
        WindowsDPAPIProtector(),
    )
    return GoogleAuthManager(
        store=store,
        oauth_flow_factory=GoogleOAuthFlowClient,
        credential_loader=load_google_credentials,
        callback_receiver_factory=LoopbackOAuthReceiver,
        operation_lock_factory=GoogleOperationLock,
        scopes=scopes,
        upgrade_from_scopes=upgrade_from_scopes,
        allowed_scopes=allowed_scopes,
    )


def build_google_auth_manager_with_calendar(
    credential_path: Path = Path("data/google/credentials.bin"),
) -> GoogleAuthManager:
    return build_google_auth_manager(
        credential_path,
        scopes=CALENDAR_SCOPES,
        upgrade_from_scopes=AUTHENTICATION_SCOPES,
        allowed_scopes=CALENDAR_SCOPES,
    )


def build_google_auth_manager_with_calendar_write(
    credential_path: Path = Path("data/google/credentials_write.bin"),
) -> GoogleAuthManager:
    return build_google_auth_manager(
        credential_path,
        scopes=CALENDAR_WRITE_SCOPES,
        upgrade_from_scopes=(),
        allowed_scopes=CALENDAR_WRITE_SCOPES,
    )
