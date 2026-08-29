"""Gerenciamento de sessões e rate limiting para Remote Gateway."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
import uuid
from typing import Callable


@dataclass(frozen=True, slots=True)
class RemoteSession:
    id: str
    device_id: str
    connected_at: str
    last_activity: float
    subscriptions: frozenset[str] = field(default_factory=frozenset)
    pending_confirmation: str | None = None  # session_id if waiting for confirmation


class RateLimiter:
    """Rate limiter simples baseado em token bucket / sliding window."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        burst_allowance: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.burst_allowance = burst_allowance
        self._clock = clock
        self._lock = threading.RLock()
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _cleanup_old(self, key: str, now: float) -> None:
        cutoff = now - self.window_seconds
        requests = self._requests[key]
        while requests and requests[0] < cutoff:
            requests.pop(0)

    def check(self, key: str) -> bool:
        """Verifica se request é permitido. Retorna True se permitido."""
        now = self._clock()
        with self._lock:
            self._cleanup_old(key, now)
            requests = self._requests[key]
            limit = self.max_requests + self.burst_allowance
            if len(requests) >= limit:
                return False
            requests.append(now)
            return True

    def get_remaining(self, key: str) -> int:
        """Retorna requests restantes na janela atual."""
        now = self._clock()
        with self._lock:
            self._cleanup_old(key, now)
            requests = self._requests[key]
            limit = self.max_requests + self.burst_allowance
            return max(0, limit - len(requests))

    def reset(self, key: str) -> None:
        with self._lock:
            if key in self._requests:
                del self._requests[key]


class SessionManager:
    """Gerencia sessões remotas ativas."""

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        max_sessions_per_device: int = 3,
        session_timeout_seconds: float = 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_limiter = rate_limiter
        self.max_sessions_per_device = max_sessions_per_device
        self.session_timeout_seconds = session_timeout_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._sessions: dict[str, RemoteSession] = {}
        self._device_sessions: dict[str, set[str]] = defaultdict(set)

    def create_session(self, device_id: str) -> RemoteSession:
        """Cria nova sessão para dispositivo."""
        with self._lock:
            # Limpa sessões antigas do dispositivo
            self._cleanup_device_sessions(device_id)
            # Verifica limite
            if len(self._device_sessions[device_id]) >= self.max_sessions_per_device:
                # Remove a mais antiga
                oldest = min(
                    self._device_sessions[device_id],
                    key=lambda sid: self._sessions[sid].last_activity,
                )
                self._remove_session_locked(oldest)
            session = RemoteSession(
                id=str(uuid.uuid4()),
                device_id=device_id,
                connected_at=datetime.now(timezone.utc).isoformat(),
                last_activity=self._clock(),
            )
            self._sessions[session.id] = session
            self._device_sessions[device_id].add(session.id)
            return session

    def get_session(self, session_id: str) -> RemoteSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def update_activity(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            self._sessions[session_id] = RemoteSession(
                id=session.id,
                device_id=session.device_id,
                connected_at=session.connected_at,
                last_activity=self._clock(),
                subscriptions=session.subscriptions,
                pending_confirmation=session.pending_confirmation,
            )
            return True

    def add_subscription(self, session_id: str, subscription: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            new_subs = set(session.subscriptions)
            new_subs.add(subscription)
            self._sessions[session_id] = RemoteSession(
                id=session.id,
                device_id=session.device_id,
                connected_at=session.connected_at,
                last_activity=session.last_activity,
                subscriptions=frozenset(new_subs),
                pending_confirmation=session.pending_confirmation,
            )
            return True

    def remove_subscription(self, session_id: str, subscription: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            new_subs = set(session.subscriptions)
            new_subs.discard(subscription)
            self._sessions[session_id] = RemoteSession(
                id=session.id,
                device_id=session.device_id,
                connected_at=session.connected_at,
                last_activity=session.last_activity,
                subscriptions=frozenset(new_subs),
                pending_confirmation=session.pending_confirmation,
            )
            return True

    def set_pending_confirmation(self, session_id: str, confirmation_session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            self._sessions[session_id] = RemoteSession(
                id=session.id,
                device_id=session.device_id,
                connected_at=session.connected_at,
                last_activity=session.last_activity,
                subscriptions=session.subscriptions,
                pending_confirmation=confirmation_session_id,
            )
            return True

    def clear_pending_confirmation(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            self._sessions[session_id] = RemoteSession(
                id=session.id,
                device_id=session.device_id,
                connected_at=session.connected_at,
                last_activity=session.last_activity,
                subscriptions=session.subscriptions,
                pending_confirmation=None,
            )
            return True

    def remove_session(self, session_id: str) -> bool:
        with self._lock:
            return self._remove_session_locked(session_id)

    def _remove_session_locked(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        self._device_sessions[session.device_id].discard(session_id)
        del self._sessions[session_id]
        return True

    def cleanup_expired(self) -> int:
        """Remove sessões expiradas. Retorna contagem removida."""
        now = self._clock()
        with self._lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if now - s.last_activity > self.session_timeout_seconds
            ]
            for sid in expired:
                self._remove_session_locked(sid)
            return len(expired)

    def _cleanup_device_sessions(self, device_id: str) -> None:
        sessions = self._device_sessions.get(device_id, set())
        expired = [
            sid for sid in sessions
            if sid in self._sessions and self._clock() - self._sessions[sid].last_activity > self.session_timeout_seconds
        ]
        for sid in expired:
            self._remove_session_locked(sid)

    def get_device_sessions(self, device_id: str) -> list[RemoteSession]:
        with self._lock:
            return [self._sessions[sid] for sid in self._device_sessions.get(device_id, set()) if sid in self._sessions]

    def list_sessions(self) -> list[RemoteSession]:
        with self._lock:
            return list(self._sessions.values())
