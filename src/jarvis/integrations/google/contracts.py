"""Contratos testáveis nas fronteiras OAuth e storage."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Event
from typing import Protocol

from jarvis.integrations.google.models import (
    ConnectionStatus,
    OAuthCallback,
    StoredCredentialMaterial,
)


class CredentialStore(Protocol):
    def load(self) -> StoredCredentialMaterial | None: ...
    def save(self, material: StoredCredentialMaterial) -> None: ...
    def delete(self) -> None: ...


class CredentialHandle(Protocol):
    @property
    def valid(self) -> bool: ...

    @property
    def expired(self) -> bool: ...

    @property
    def scopes(self) -> tuple[str, ...]: ...

    @property
    def access_token(self) -> str: ...

    def refresh(self) -> None: ...
    def revoke(self) -> None: ...
    def storage_material(self) -> StoredCredentialMaterial: ...


class OAuthFlowClient(Protocol):
    def authorization_url(self) -> tuple[str, str]: ...
    def exchange(self, authorization_response: str) -> CredentialHandle: ...


class OAuthCallbackReceiver(AbstractContextManager["OAuthCallbackReceiver"], Protocol):
    @property
    def redirect_uri(self) -> str: ...

    def wait(
        self,
        timeout_seconds: float,
        cancel: Event | None,
        expected_state: str,
    ) -> OAuthCallback: ...
    def close(self) -> None: ...


OAuthFlowFactory = Callable[[Path, tuple[str, ...], str, str], OAuthFlowClient]
CredentialLoader = Callable[[StoredCredentialMaterial], CredentialHandle]
CallbackReceiverFactory = Callable[[], OAuthCallbackReceiver]
OperationLockFactory = Callable[[], AbstractContextManager[object]]


class GoogleAuthProvider(Protocol):
    def connect(
        self,
        client_config_path: Path,
        *,
        timeout_seconds: float = 120.0,
        cancel: Event | None = None,
    ) -> ConnectionStatus: ...

    def status(self) -> ConnectionStatus: ...
    def disconnect(self) -> ConnectionStatus: ...
    def credentials(self) -> CredentialHandle: ...
    def refresh_credentials(self) -> CredentialHandle: ...
