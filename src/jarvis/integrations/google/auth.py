"""Lifecycle de autenticação Google sem acoplamento ao Harness."""

from __future__ import annotations

from hmac import compare_digest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import secrets
import threading
from threading import Event
import webbrowser

from jarvis.integrations.google.contracts import (
    CallbackReceiverFactory,
    CredentialHandle,
    CredentialLoader,
    CredentialStore,
    OAuthFlowFactory,
    OperationLockFactory,
)
from jarvis.integrations.google.errors import (
    GoogleAuthorizationDeniedError,
    GoogleBrowserLaunchError,
    GoogleCredentialStorageCorruptError,
    GoogleCredentialStorageError,
    GoogleIntegrationError,
    GoogleNetworkError,
    GoogleOAuthStateError,
    GoogleRevocationError,
    GoogleScopeError,
    GoogleTokenRevokedError,
)
from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
from jarvis.integrations.google.scopes import AUTHENTICATION_SCOPES


class GoogleAuthManager:
    def __init__(
        self,
        store: CredentialStore,
        oauth_flow_factory: OAuthFlowFactory,
        credential_loader: CredentialLoader,
        callback_receiver_factory: CallbackReceiverFactory,
        *,
        browser_open=webbrowser.open,
        scopes: tuple[str, ...] = AUTHENTICATION_SCOPES,
        operation_lock_factory: OperationLockFactory = nullcontext,
    ) -> None:
        self._store = store
        self._oauth_flow_factory = oauth_flow_factory
        self._credential_loader = credential_loader
        self._callback_receiver_factory = callback_receiver_factory
        self._browser_open = browser_open
        self._scopes = tuple(scopes)
        self._operation_lock_factory = operation_lock_factory
        self._handle: CredentialHandle | None = None
        self._status = ConnectionStatus(ConnectionState.DISCONNECTED)
        self._lock = threading.RLock()

    @property
    def current_status(self) -> ConnectionStatus:
        # ConnectionStatus is immutable; this remains observable while a
        # refresh call holds the operation lock.
        return self._status

    def connect(
        self,
        client_config_path: Path,
        *,
        timeout_seconds: float = 120.0,
        cancel: Event | None = None,
    ) -> ConnectionStatus:
        with self._operation_lock_factory(), self._lock:
            existing = self._load_and_refresh()
            if existing.state is ConnectionState.CONNECTED:
                return existing
            if existing.error_code == "revocation_pending":
                raise GoogleRevocationError(
                    "Revogação Google pendente; tente disconnect novamente."
                )
            state = secrets.token_urlsafe(32)
            with self._callback_receiver_factory() as receiver:
                flow = self._oauth_flow_factory(
                    client_config_path,
                    self._scopes,
                    state,
                    receiver.redirect_uri,
                )
                authorization_url, flow_state = flow.authorization_url()
                if not compare_digest(state, flow_state):
                    self._status = ConnectionStatus(
                        ConnectionState.ERROR, error_code="invalid_state"
                    )
                    raise GoogleOAuthStateError(
                        "OAuth Google retornou state inválido."
                    )
                try:
                    browser_opened = self._browser_open(authorization_url)
                except Exception:
                    browser_opened = False
                if not browser_opened:
                    raise GoogleBrowserLaunchError(
                        "Não foi possível abrir o navegador para consentimento Google."
                    )
                callback = receiver.wait(timeout_seconds, cancel, state)
                if callback.state is None or not compare_digest(state, callback.state):
                    self._status = ConnectionStatus(
                        ConnectionState.ERROR, error_code="invalid_state"
                    )
                    raise GoogleOAuthStateError(
                        "Callback OAuth Google contém state inválido."
                    )
                if callback.error is not None:
                    self._status = ConnectionStatus(
                        ConnectionState.AUTHORIZATION_REQUIRED,
                        error_code="access_denied",
                    )
                    raise GoogleAuthorizationDeniedError(
                        "Autorização Google negada pelo usuário."
                    )
                handle = flow.exchange(callback.authorization_response)
            try:
                self._require_scopes(handle)
            except GoogleScopeError:
                self._quarantine_for_revocation(handle)
                self._status = ConnectionStatus(
                    ConnectionState.ERROR, error_code="credential_scope_error"
                )
                raise
            self._store.save(handle.storage_material())
            self._handle = handle
            self._status = ConnectionStatus(
                ConnectionState.CONNECTED, scopes=handle.scopes
            )
            return self._status

    def status(self) -> ConnectionStatus:
        with self._operation_lock_factory(), self._lock:
            return self._load_and_refresh()

    def credentials(self) -> CredentialHandle:
        with self._operation_lock_factory(), self._lock:
            status = self._load_and_refresh()
            if status.state is not ConnectionState.CONNECTED or self._handle is None:
                raise GoogleTokenRevokedError(
                    "Autorização Google é necessária."
                )
            return self._handle

    def disconnect(self) -> ConnectionStatus:
        with self._operation_lock_factory(), self._lock:
            handle = self._handle
            if handle is None:
                try:
                    material = self._store.load()
                    if material is not None:
                        handle = self._credential_loader(material)
                except GoogleCredentialStorageCorruptError:
                    handle = None
            remote_error = False
            if handle is not None:
                try:
                    self._quarantine_for_revocation(handle)
                except GoogleCredentialStorageError:
                    self._store.delete()
                    self._handle = None
                    self._status = ConnectionStatus(
                        ConnectionState.DISCONNECTED,
                        error_code="remote_revocation_unconfirmed_local_removed",
                    )
                    return self._status
                try:
                    handle.revoke()
                except (GoogleRevocationError, GoogleNetworkError):
                    remote_error = True
                if not remote_error:
                    self._store.delete()
            else:
                self._store.delete()
            self._handle = None
            self._status = ConnectionStatus(
                ConnectionState.DISCONNECTED,
                error_code="remote_revocation_unconfirmed" if remote_error else None,
            )
            return self._status

    def _load_and_refresh(self) -> ConnectionStatus:
        if self._handle is not None and self._handle.valid:
            try:
                self._require_scopes(self._handle)
            except GoogleScopeError:
                self._quarantine_for_revocation(self._handle)
                self._handle = None
                self._status = ConnectionStatus(
                    ConnectionState.ERROR, error_code="credential_scope_error"
                )
                return self._status
            self._status = ConnectionStatus(ConnectionState.CONNECTED, scopes=self._handle.scopes)
            return self._status
        if self._handle is None:
            try:
                material = self._store.load()
            except GoogleCredentialStorageCorruptError:
                self._status = ConnectionStatus(
                    ConnectionState.ERROR, error_code="storage_corrupt"
                )
                return self._status
            if material is None:
                if self._status.state not in {
                    ConnectionState.REVOKED,
                    ConnectionState.AUTHORIZATION_REQUIRED,
                }:
                    self._status = ConnectionStatus(ConnectionState.DISCONNECTED)
                return self._status
            if material.revocation_pending:
                self._status = ConnectionStatus(
                    ConnectionState.ERROR,
                    error_code="revocation_pending",
                )
                return self._status
            self._handle = self._credential_loader(material)
        self._status = ConnectionStatus(
            ConnectionState.TOKEN_REFRESHING, scopes=self._handle.scopes
        )
        try:
            self._handle.refresh()
            self._require_scopes(self._handle)
            self._store.save(self._handle.storage_material())
        except GoogleTokenRevokedError:
            self._store.delete()
            self._handle = None
            self._status = ConnectionStatus(
                ConnectionState.REVOKED, error_code="token_revoked"
            )
            return self._status
        except GoogleScopeError:
            self._quarantine_for_revocation(self._handle)
            self._handle = None
            self._status = ConnectionStatus(
                ConnectionState.ERROR, error_code="credential_scope_error"
            )
            return self._status
        except GoogleNetworkError:
            self._status = ConnectionStatus(
                ConnectionState.ERROR, error_code="network_error"
            )
            return self._status
        except GoogleIntegrationError:
            self._status = ConnectionStatus(
                ConnectionState.ERROR, error_code="credential_error"
            )
            return self._status
        self._status = ConnectionStatus(
            ConnectionState.CONNECTED, scopes=self._handle.scopes
        )
        return self._status

    def _require_scopes(self, handle: CredentialHandle) -> None:
        if not set(self._scopes).issubset(handle.scopes):
            raise GoogleScopeError(
                "Google não concedeu todos os scopes mínimos solicitados."
            )

    def _quarantine_for_revocation(self, handle: CredentialHandle) -> None:
        pending = replace(
            handle.storage_material(),
            revocation_pending=True,
        )
        try:
            self._store.save(pending)
        except GoogleCredentialStorageError:
            # If quarantine cannot be persisted, remove local material so a
            # later process cannot refresh it as an active credential.
            self._store.delete()
            raise
