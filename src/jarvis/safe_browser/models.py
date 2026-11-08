from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class BrowserDestination:
    id: str
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class ValidatedUri:
    canonical_url: str
    destination_id: str
    association_token: str
    _proof: object = field(repr=False, compare=False)
    _integrity: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PreparedBrowserLaunch:
    request: ValidatedUri
    action: str
    provider: str | None = None
    term: str | None = field(default=None, repr=False)

    def binding_digest(self) -> str:
        term_hash = sha256((self.term or "").encode("utf-8")).hexdigest()
        value = (
            f"{self.action}|{self.request.destination_id}|"
            f"{self.request.canonical_url}|{self.request.association_token}|"
            f"{self.provider or ''}|{term_hash}"
        )
        return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthorizedBrowserLaunch:
    prepared: PreparedBrowserLaunch
