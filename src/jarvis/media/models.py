from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any


class PlaybackState(str, Enum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    CLOSED = "closed"
    CHANGING = "changing"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class MediaCapabilities:
    play: bool = False
    pause: bool = False
    next: bool = False
    previous: bool = False

    def permits(self, action: str) -> bool:
        return bool(getattr(self, action, False))


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    source: str | None = None
    title: str | None = None
    artist: str | None = None
    position: timedelta | None = None
    duration: timedelta | None = None


@dataclass(frozen=True, slots=True)
class MediaSnapshot:
    state: PlaybackState
    capabilities: MediaCapabilities
    metadata: MediaMetadata


@dataclass(frozen=True, slots=True)
class SessionBinding:
    identity: str
    handle: Any
    snapshot: MediaSnapshot


@dataclass(frozen=True, slots=True)
class PreparedMediaOperation:
    action: str
    session: SessionBinding
