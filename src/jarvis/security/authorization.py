"""Autorizações efêmeras, vinculadas e de uso único."""

from dataclasses import dataclass
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
    ) -> AuthorizationGrant:
        now = self._clock()
        grant = AuthorizationGrant(
            id=str(uuid4()),
            tool_name=tool_name,
            canonical_target=canonicalize_target(target),
            interaction_id=interaction_id or str(uuid4()),
            created_at=now,
            expires_at=now + timedelta(seconds=self.lifetime_seconds),
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
            )
            if not valid:
                return False
            object.__setattr__(grant, "consumed", True)
            self._grants.pop(grant.id, None)
            return True

    def invalidate_all(self) -> None:
        with self._lock:
            for grant in self._grants.values():
                object.__setattr__(grant, "consumed", True)
            self._grants.clear()
