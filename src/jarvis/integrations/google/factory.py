"""Composição local da fundação Google."""

from __future__ import annotations

import os
from pathlib import Path
import secrets

from jarvis.integrations.google.auth import GoogleAuthManager
from jarvis.integrations.google.client import (
    GoogleOAuthFlowClient,
    load_google_credentials,
)
from jarvis.integrations.google.loopback import LoopbackOAuthReceiver
from jarvis.integrations.google.operation_lock import GoogleOperationLock
from jarvis.integrations.google.scopes import (
    AUTHENTICATION_SCOPES,
    CALENDAR_SCOPES,
    CALENDAR_MINIMAL_READ_SCOPES,
    CALENDAR_WRITE_SCOPES,
    CALENDAR_LIMITED_WRITE_SCOPES,
    DRIVE_DOCS_SELECTED_SCOPES,
    DRIVE_PICKER_SCOPES,
)
from jarvis.integrations.google.storage import (
    ProtectedFileCredentialStore,
    WindowsDPAPIProtector,
)


def google_credential_dir() -> Path:
    override = os.environ.get("JARVIS_GOOGLE_CREDENTIAL_DIR")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "JARVIS" / "google"
    return Path.home() / ".jarvis" / "google"


def _resolve_credential_path(
    filename: str,
    credential_path: Path | None,
) -> Path:
    if credential_path is not None:
        return credential_path
    stable = google_credential_dir() / filename
    if stable.is_file():
        return stable

    legacy = Path("data/google") / filename
    if legacy.is_file():
        stable.parent.mkdir(parents=True, exist_ok=True)
        temporary = stable.with_name(
            f".{stable.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temporary.write_bytes(legacy.read_bytes())
            temporary.replace(stable)
        finally:
            temporary.unlink(missing_ok=True)
    return stable


def build_google_auth_manager(
    credential_path: Path | None = None,
    scopes: tuple[str, ...] = AUTHENTICATION_SCOPES,
    upgrade_from_scopes: tuple[str, ...] = (),
    allowed_scopes: tuple[str, ...] | None = None,
) -> GoogleAuthManager:
    resolved = _resolve_credential_path("credentials.bin", credential_path)
    store = ProtectedFileCredentialStore(
        resolved,
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
    credential_path: Path | None = None,
) -> GoogleAuthManager:
    resolved = _resolve_credential_path("credentials.bin", credential_path)
    return build_google_auth_manager(
        resolved,
        scopes=CALENDAR_SCOPES,
        upgrade_from_scopes=AUTHENTICATION_SCOPES,
        allowed_scopes=CALENDAR_SCOPES,
    )


def build_google_calendar_minimal_read_auth(
    credential_path: Path | None = None,
) -> GoogleAuthManager:
    # Separate file: never quarantine, revoke or overwrite a legacy broader grant.
    path = credential_path or google_credential_dir() / "credentials_calendar_read.bin"
    return build_google_auth_manager(
        path,
        scopes=CALENDAR_MINIMAL_READ_SCOPES,
        allowed_scopes=CALENDAR_MINIMAL_READ_SCOPES,
    )


def build_google_drive_docs_auth(credential_path: Path | None = None) -> GoogleAuthManager:
    path = credential_path or google_credential_dir() / "credentials_drive_docs_read.bin"
    return build_google_auth_manager(path, scopes=DRIVE_PICKER_SCOPES,
                                     allowed_scopes=DRIVE_DOCS_SELECTED_SCOPES)


def drive_docs_grant_binding(credential_path: Path | None = None) -> str:
    """Local grant identity, without refresh/network or exposing credential material."""
    path = credential_path or google_credential_dir() / "credentials_drive_docs_read.bin"
    material = ProtectedFileCredentialStore(path, WindowsDPAPIProtector()).load()
    if (material is None or material.revocation_pending
            or set(material.scopes) not in (set(DRIVE_PICKER_SCOPES), set(DRIVE_DOCS_SELECTED_SCOPES))):
        raise PermissionError("Grant Drive/Docs ausente ou inválido; conecte explicitamente.")
    from jarvis.integrations.google.drive_docs.client import grant_digest
    return grant_digest(material)


def build_google_auth_manager_with_calendar_write(
    credential_path: Path | None = None,
) -> GoogleAuthManager:
    resolved = _resolve_credential_path("credentials_write.bin", credential_path)
    return build_google_auth_manager(
        resolved,
        scopes=CALENDAR_WRITE_SCOPES,
        upgrade_from_scopes=(),
        allowed_scopes=CALENDAR_WRITE_SCOPES,
    )


def calendar_write_grant_binding(credential_path: Path | None = None) -> str:
    """Bind consent to the separate local grant; never log credential material."""
    import hashlib
    import json

    path = _resolve_credential_path("credentials_calendar_limited_write.bin", credential_path)
    material = ProtectedFileCredentialStore(path, WindowsDPAPIProtector()).load()
    if (material is None or material.revocation_pending
            or set(material.scopes) != set(CALENDAR_LIMITED_WRITE_SCOPES)):
        raise PermissionError("Consentimento Calendar WRITE separado ausente ou insuficiente.")
    return hashlib.sha256(json.dumps(material.to_dict(), sort_keys=True).encode()).hexdigest()


def build_google_calendar_limited_write_auth(
    credential_path: Path | None = None,
) -> GoogleAuthManager:
    path = _resolve_credential_path("credentials_calendar_limited_write.bin", credential_path)
    return build_google_auth_manager(path, scopes=CALENDAR_LIMITED_WRITE_SCOPES,
                                     upgrade_from_scopes=(), allowed_scopes=CALENDAR_LIMITED_WRITE_SCOPES)
