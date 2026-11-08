"""Opaque, purpose-bound secret references with short-lived leases."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable, Generic, TypeVar
from uuid import uuid4


T = TypeVar("T")
AuditSink = Callable[[str, str, str], None]


@dataclass(frozen=True, slots=True)
class SecretReference:
    id: str
    label: str

    def __str__(self) -> str:
        return f"secret://{self.id}"

    def __repr__(self) -> str:
        return f"SecretReference(id={self.id!r}, label={self.label!r})"


@dataclass(frozen=True, slots=True)
class SecretCapability:
    reference_id: str
    purpose: str
    expires_at: datetime
    nonce: str = field(repr=False)


class _SecretLease(AbstractContextManager[str]):
    def __init__(
        self,
        value: str,
        *,
        on_close: Callable[[], None],
    ) -> None:
        self._value = value
        self._on_close = on_close
        self._closed = False

    def __enter__(self) -> str:
        if self._closed:
            raise RuntimeError("secret lease is closed")
        return self._value

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._value = ""
        self._closed = True
        self._on_close()

    def __repr__(self) -> str:
        return "SecretLease(<redacted>)"


class SecretBroker:
    """Keep plaintext secrets behind opaque references and purpose-bound grants."""

    def __init__(
        self,
        *,
        capability_lifetime_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.capability_lifetime_seconds = capability_lifetime_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._audit_sink = audit_sink
        self._values: dict[str, str] = {}
        self._capabilities: dict[str, SecretCapability] = {}
        self._lock = Lock()

    def register(self, label: str, value: str) -> SecretReference:
        if not label.strip() or not value:
            raise ValueError("secret label/value is required")
        reference = SecretReference(uuid4().hex, label.strip())
        with self._lock:
            self._values[reference.id] = value
        self._audit(reference.id, "register", "ok")
        return reference

    def issue_capability(
        self,
        reference: SecretReference,
        *,
        purpose: str,
    ) -> SecretCapability:
        if not purpose.strip():
            raise ValueError("secret purpose is required")
        with self._lock:
            if reference.id not in self._values:
                raise KeyError("unknown secret reference")
            capability = SecretCapability(
                reference_id=reference.id,
                purpose=purpose.strip(),
                expires_at=self._clock()
                + timedelta(seconds=self.capability_lifetime_seconds),
                nonce=uuid4().hex,
            )
            self._capabilities[capability.nonce] = capability
        self._audit(reference.id, f"capability:{capability.purpose}", "issued")
        return capability

    def lease(
        self,
        reference: SecretReference,
        capability: SecretCapability,
        *,
        purpose: str,
    ) -> AbstractContextManager[str]:
        with self._lock:
            stored = self._capabilities.get(capability.nonce)
            valid = (
                stored is capability
                and capability.reference_id == reference.id
                and capability.purpose == purpose
                and self._clock() < capability.expires_at
                and reference.id in self._values
            )
            if not valid:
                self._audit(reference.id, f"lease:{purpose}", "denied")
                raise PermissionError("secret capability denied")
            value = self._values[reference.id]
            self._capabilities.pop(capability.nonce, None)

        self._audit(reference.id, f"lease:{purpose}", "opened")
        return _SecretLease(
            value,
            on_close=lambda: self._audit(
                reference.id, f"lease:{purpose}", "closed"
            ),
        )

    def revoke(self, reference: SecretReference) -> None:
        with self._lock:
            self._values.pop(reference.id, None)
            stale = [
                nonce
                for nonce, capability in self._capabilities.items()
                if capability.reference_id == reference.id
            ]
            for nonce in stale:
                self._capabilities.pop(nonce, None)
        self._audit(reference.id, "revoke", "ok")

    def _audit(self, reference_id: str, action: str, status: str) -> None:
        sink = self._audit_sink
        if sink is None:
            return
        sink(reference_id, action, status)
