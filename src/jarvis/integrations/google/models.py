"""Modelos agregados e materiais OAuth não representáveis em logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    AUTHORIZATION_REQUIRED = "authorization_required"
    CONNECTED = "connected"
    TOKEN_REFRESHING = "token_refreshing"
    REVOKED = "revoked"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    state: ConnectionState
    scopes: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class StoredCredentialMaterial:
    refresh_token: str = field(repr=False)
    token_uri: str = field(repr=False)
    client_id: str = field(repr=False)
    client_secret: str | None = field(default=None, repr=False)
    scopes: tuple[str, ...] = ()
    revocation_pending: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "refresh_token": self.refresh_token,
            "token_uri": self.token_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scopes": list(self.scopes),
            "revocation_pending": self.revocation_pending,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StoredCredentialMaterial:
        expected = {
            "version",
            "refresh_token",
            "token_uri",
            "client_id",
            "client_secret",
            "scopes",
            "revocation_pending",
        }
        if set(value) != expected or value.get("version") != 1:
            raise ValueError("schema")
        scopes = value.get("scopes")
        client_secret = value.get("client_secret")
        revocation_pending = value.get("revocation_pending")
        required = (
            value.get("refresh_token"),
            value.get("token_uri"),
            value.get("client_id"),
        )
        if not all(isinstance(item, str) and item for item in required):
            raise ValueError("fields")
        if client_secret is not None and not isinstance(client_secret, str):
            raise ValueError("client_secret")
        if not isinstance(revocation_pending, bool):
            raise ValueError("revocation_pending")
        if not isinstance(scopes, list) or not all(
            isinstance(scope, str) and scope for scope in scopes
        ):
            raise ValueError("scopes")
        return cls(
            refresh_token=value["refresh_token"],
            token_uri=value["token_uri"],
            client_id=value["client_id"],
            client_secret=client_secret,
            scopes=tuple(scopes),
            revocation_pending=revocation_pending,
        )


@dataclass(frozen=True, slots=True)
class OAuthCallback:
    authorization_response: str = field(repr=False)
    state: str | None = field(repr=False)
    error: str | None = None
