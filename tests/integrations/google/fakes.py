from pathlib import Path
from threading import Event

from jarvis.integrations.google.errors import (
    GoogleAuthorizationCancelledError,
    GoogleAuthorizationTimeoutError,
    GoogleNetworkError,
    GoogleRevocationError,
    GoogleTokenRevokedError,
)
from jarvis.integrations.google.models import OAuthCallback, StoredCredentialMaterial


MATERIAL = StoredCredentialMaterial(
    refresh_token="refresh-secret",
    token_uri="https://oauth2.googleapis.com/token",
    client_id="client.apps.googleusercontent.com",
    client_secret="client-secret",
    scopes=("openid",),
)


class FakeStore:
    def __init__(self, material: StoredCredentialMaterial | None = None) -> None:
        self.material = material
        self.saved: list[StoredCredentialMaterial] = []
        self.delete_calls = 0

    def load(self) -> StoredCredentialMaterial | None:
        return self.material

    def save(self, material: StoredCredentialMaterial) -> None:
        self.material = material
        self.saved.append(material)

    def delete(self) -> None:
        self.material = None
        self.delete_calls += 1


class FakeHandle:
    def __init__(
        self,
        *,
        valid: bool = True,
        scopes: tuple[str, ...] = ("openid",),
        refresh_error: str | None = None,
        revoke_error: bool = False,
        refresh_started: Event | None = None,
        refresh_release: Event | None = None,
    ) -> None:
        self._valid = valid
        self._scopes = scopes
        self.refresh_error = refresh_error
        self.revoke_error = revoke_error
        self.refresh_calls = 0
        self.revoke_calls = 0
        self.refresh_started = refresh_started
        self.refresh_release = refresh_release

    @property
    def valid(self) -> bool:
        return self._valid

    @property
    def expired(self) -> bool:
        return not self._valid

    @property
    def scopes(self) -> tuple[str, ...]:
        return self._scopes

    def refresh(self) -> None:
        self.refresh_calls += 1
        if self.refresh_started is not None:
            self.refresh_started.set()
        if self.refresh_release is not None:
            self.refresh_release.wait(2.0)
        if self.refresh_error == "revoked":
            raise GoogleTokenRevokedError("safe")
        if self.refresh_error == "network":
            raise GoogleNetworkError("safe")
        self._valid = True

    def revoke(self) -> None:
        self.revoke_calls += 1
        if self.revoke_error:
            raise GoogleRevocationError("safe")

    def storage_material(self) -> StoredCredentialMaterial:
        return StoredCredentialMaterial(
            refresh_token=MATERIAL.refresh_token,
            token_uri=MATERIAL.token_uri,
            client_id=MATERIAL.client_id,
            client_secret=MATERIAL.client_secret,
            scopes=self.scopes,
        )


class FakeFlow:
    def __init__(self, state: str, handle: FakeHandle) -> None:
        self.state = state
        self.handle = handle
        self.exchange_calls: list[str] = []

    def authorization_url(self) -> tuple[str, str]:
        return "https://accounts.google.com/authorize", self.state

    def exchange(self, authorization_response: str) -> FakeHandle:
        self.exchange_calls.append(authorization_response)
        return self.handle


class FakeReceiver:
    redirect_uri = "http://127.0.0.1:54321/oauth2/callback"

    def __init__(
        self,
        callback: OAuthCallback | None = None,
        error: str | None = None,
    ) -> None:
        self.callback = callback
        self.error = error
        self.closed = 0

    def wait(
        self,
        _timeout_seconds: float,
        cancel: Event | None,
        _expected_state: str,
    ) -> OAuthCallback:
        if cancel is not None and cancel.is_set():
            raise GoogleAuthorizationCancelledError("safe")
        if self.error == "timeout":
            raise GoogleAuthorizationTimeoutError("safe")
        assert self.callback is not None
        return self.callback

    def close(self) -> None:
        self.closed += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
