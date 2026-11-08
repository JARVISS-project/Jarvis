"""Modelo de dados do Control Center - agregação read-only para UI."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from jarvis.application.telemetry import TelemetrySnapshot
from jarvis.memory.models import MemoryEntry, MemorySensitivity
from jarvis.skills.models import SkillDefinition
from jarvis.vision.models import TextResult, UITreeResult


class ScreenChange(str, Enum):
    BASELINE = "baseline"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class MemorySummary:
    total: int
    by_sensitivity: dict[str, int]
    with_consent: int
    expired: int
    recent: list[MemoryEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SkillSummary:
    total: int
    total_executions: int
    total_successes: int
    recent: list[SkillDefinition] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VisionSummary:
    enabled: bool
    last_observation: ScreenChange | None = None
    last_ocr: TextResult | None = None
    last_ui_tree: UITreeResult | None = None


@dataclass(frozen=True, slots=True)
class ControlCenterSnapshot:
    """Snapshot agregado do estado do Control Center para UI."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    telemetry: TelemetrySnapshot = field(default_factory=TelemetrySnapshot)
    memory: MemorySummary = field(default_factory=lambda: MemorySummary(0, {}, 0, 0))
    skills: SkillSummary = field(default_factory=lambda: SkillSummary(0, 0, 0))
    vision: VisionSummary = field(default_factory=lambda: VisionSummary(False))


class ControlCenterBus:
    """Barramento para publicar snapshots do Control Center."""

    def __init__(self) -> None:
        from threading import RLock
        self._lock = RLock()
        self._snapshot = ControlCenterSnapshot()
        self._subscribers: list[callable] = []

    @property
    def snapshot(self) -> ControlCenterSnapshot:
        with self._lock:
            return self._snapshot

    def update_telemetry(self, telemetry: TelemetrySnapshot) -> None:
        with self._lock:
            self._snapshot = ControlCenterSnapshot(
                timestamp=datetime.now(timezone.utc),
                telemetry=telemetry,
                memory=self._snapshot.memory,
                skills=self._snapshot.skills,
                vision=self._snapshot.vision,
            )
        self._notify()

    def update_memory(self, memory: MemorySummary) -> None:
        with self._lock:
            self._snapshot = ControlCenterSnapshot(
                timestamp=datetime.now(timezone.utc),
                telemetry=self._snapshot.telemetry,
                memory=memory,
                skills=self._snapshot.skills,
                vision=self._snapshot.vision,
            )
        self._notify()

    def update_skills(self, skills: SkillSummary) -> None:
        with self._lock:
            self._snapshot = ControlCenterSnapshot(
                timestamp=datetime.now(timezone.utc),
                telemetry=self._snapshot.telemetry,
                memory=self._snapshot.memory,
                skills=skills,
                vision=self._snapshot.vision,
            )
        self._notify()

    def update_vision(self, vision: VisionSummary) -> None:
        with self._lock:
            self._snapshot = ControlCenterSnapshot(
                timestamp=datetime.now(timezone.utc),
                telemetry=self._snapshot.telemetry,
                memory=self._snapshot.memory,
                skills=self._snapshot.skills,
                vision=vision,
            )
        self._notify()

    def subscribe(self, subscriber: callable) -> callable:
        with self._lock:
            self._subscribers.append(subscriber)
        subscriber(self._snapshot)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)
        return unsubscribe

    def _notify(self) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(self._snapshot)
            except Exception:
                continue