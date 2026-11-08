"""Fail-closed network egress policy and bounded execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import urlparse


T = TypeVar("T")
AuditSink = Callable[[str, str, str], None]


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    UNKNOWN = "unknown"
    SECRET = "secret"
    RESTRICTED = "restricted"


@dataclass(frozen=True, slots=True)
class EgressPermit:
    destination: str
    purpose: str
    classification: DataClassification


@dataclass(frozen=True, slots=True)
class DownloadArtifact:
    path: Path
    source: str
    trust: str = "untrusted"


class EgressPolicy:
    """Authorize only explicit, classifiable outbound network use."""

    def __init__(
        self,
        *,
        allowed_hosts: set[str] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.allowed_hosts = {
            host.rstrip(".").casefold() for host in (allowed_hosts or set())
        }
        self._audit_sink = audit_sink

    def authorize(
        self,
        destination: str,
        *,
        purpose: str,
        classification: DataClassification,
    ) -> EgressPermit:
        parsed = urlparse(destination)
        host = (parsed.hostname or "").rstrip(".").casefold()
        allowed_class = classification in {
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
        }
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(host)
            and parsed.username is None
            and parsed.password is None
            and bool(purpose.strip())
            and allowed_class
            and (
                not self.allowed_hosts
                or any(
                    host == rule or host.endswith(f".{rule}")
                    for rule in self.allowed_hosts
                )
            )
        )
        if not valid:
            self._audit(host or "<invalid>", purpose, "denied")
            raise PermissionError("network egress denied")
        self._audit(host, purpose, "authorized")
        return EgressPermit(destination, purpose.strip(), classification)

    def run_bounded(
        self,
        permit: EgressPermit,
        operation: Callable[[], T],
        *,
        max_attempts: int = 2,
        verify: Callable[[T], bool] | None = None,
    ) -> T:
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")
        host = urlparse(permit.destination).hostname or "<invalid>"
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            self._audit(host, permit.purpose, f"attempt:{attempt}")
            try:
                result = operation()
                if verify is not None and not verify(result):
                    raise RuntimeError("external result verification failed")
                self._audit(host, permit.purpose, "verified")
                return result
            except Exception as error:
                last_error = error
                self._audit(host, permit.purpose, f"failed:{attempt}")
        assert last_error is not None
        raise last_error

    def _audit(self, target: str, purpose: str, status: str) -> None:
        if self._audit_sink is not None:
            self._audit_sink(target, purpose, status)
