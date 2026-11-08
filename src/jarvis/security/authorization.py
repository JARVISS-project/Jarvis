"""Autorizações efêmeras, vinculadas e de uso único."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable
from uuid import uuid4


def canonicalize_target(target: str) -> str:
    return target.strip().replace("\\", "/").casefold()


@dataclass(frozen=True)
class AuthorizationGrant:
    id: str
    tool_name: str
    canonical_target: str
    interaction_id: str
    created_at: datetime
    expires_at: datetime
    consumed: bool = False
    session_id: str | None = None
    arguments_digest: str | None = None
    preparation_digest: str | None = field(default=None, repr=False)


_RECEIPT_AUTHORITY = object()
_RECEIPT_REGISTRY: dict[int, "AuthorizationReceipt"] = {}
_RECEIPT_REGISTRY_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class AuthorizationReceipt:
    """One-use proof that a matching grant was consumed by the executor."""

    tool_name: str
    canonical_target: str
    preparation_digest: str | None = field(default=None, repr=False)
    _authority: object = field(default=None, repr=False, compare=False)
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)
    _claimed: list[bool] = field(
        default_factory=lambda: [False], repr=False, compare=False
    )

    def claim(
        self,
        tool_name: str,
        target: str,
        preparation_digest: str | None,
    ) -> bool:
        with _RECEIPT_REGISTRY_LOCK:
            if _RECEIPT_REGISTRY.get(id(self)) is not self:
                return False
            with self._lock:
                if (
                    self._authority is not _RECEIPT_AUTHORITY
                    or self._claimed[0]
                    or self.tool_name != tool_name
                    or self.canonical_target != canonicalize_target(target)
                    or self.preparation_digest != preparation_digest
                ):
                    return False
                self._claimed[0] = True
                _RECEIPT_REGISTRY.pop(id(self), None)
                return True

    def invalidate(self) -> None:
        with _RECEIPT_REGISTRY_LOCK:
            if _RECEIPT_REGISTRY.get(id(self)) is self:
                _RECEIPT_REGISTRY.pop(id(self), None)


class AuthorizationGrantStore:
    """Emite e consome grants sem expô-los ao LLM ou ao Harness."""

    def __init__(
        self,
        lifetime_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.lifetime_seconds = lifetime_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._grants: dict[str, AuthorizationGrant] = {}
        self._lock = Lock()

    def issue(
        self,
        tool_name: str,
        target: str,
        interaction_id: str | None = None,
        session_id: str | None = None,
        arguments_digest: str | None = None,
        preparation_digest: str | None = None,
    ) -> AuthorizationGrant:
        now = self._clock()
        grant = AuthorizationGrant(
            id=str(uuid4()),
            tool_name=tool_name,
            canonical_target=canonicalize_target(target),
            interaction_id=interaction_id or str(uuid4()),
            created_at=now,
            expires_at=now + timedelta(seconds=self.lifetime_seconds),
            session_id=session_id,
            arguments_digest=arguments_digest,
            preparation_digest=preparation_digest,
        )
        with self._lock:
            self._grants[grant.id] = grant
        return grant

    def consume(
        self,
        grant: AuthorizationGrant,
        tool_name: str,
        target: str,
        interaction_id: str | None = None,
        session_id: str | None = None,
        arguments_digest: str | None = None,
        preparation_digest: str | None = None,
    ) -> bool:
        with self._lock:
            stored = self._grants.get(grant.id)
            valid = (
                stored is grant
                and not grant.consumed
                and self._clock() < grant.expires_at
                and grant.tool_name == tool_name
                and grant.canonical_target == canonicalize_target(target)
                and (
                    interaction_id is None
                    or grant.interaction_id == interaction_id
                )
                and grant.session_id == session_id
                and grant.arguments_digest == arguments_digest
                and grant.preparation_digest == preparation_digest
            )
            if not valid:
                return False
            object.__setattr__(grant, "consumed", True)
            self._grants.pop(grant.id, None)
            return True

    def consume_for_execution(
        self,
        grant: AuthorizationGrant,
        tool_name: str,
        target: str,
        interaction_id: str | None = None,
        session_id: str | None = None,
        arguments_digest: str | None = None,
        preparation_digest: str | None = None,
    ) -> AuthorizationReceipt | None:
        if not self.consume(
            grant,
            tool_name,
            target,
            interaction_id,
            session_id,
            arguments_digest,
            preparation_digest,
        ):
            return None
        receipt = AuthorizationReceipt(
            tool_name=tool_name,
            canonical_target=canonicalize_target(target),
            preparation_digest=preparation_digest,
            _authority=_RECEIPT_AUTHORITY,
        )
        with _RECEIPT_REGISTRY_LOCK:
            _RECEIPT_REGISTRY[id(receipt)] = receipt
        return receipt

    def invalidate_all(self) -> None:
        with self._lock:
            for grant in self._grants.values():
                object.__setattr__(grant, "consumed", True)
            self._grants.clear()

    def invalidate_session(self, session_id: str) -> None:
        with self._lock:
            matching = [
                grant_id
                for grant_id, grant in self._grants.items()
                if grant.session_id == session_id
            ]
            for grant_id in matching:
                grant = self._grants.pop(grant_id)
                object.__setattr__(grant, "consumed", True)
