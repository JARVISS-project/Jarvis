"""Testes para o modelo do Control Center."""

from datetime import datetime, timezone

from jarvis.application.telemetry import TelemetrySnapshot
from jarvis.memory.models import MemoryEntry, MemorySensitivity
from jarvis.skills.models import SkillDefinition, SkillStep
from jarvis.ui.control_center_model import (
    ControlCenterBus,
    ControlCenterSnapshot,
    MemorySummary,
    ScreenChange,
    SkillSummary,
    VisionSummary,
)


def test_control_center_snapshot_default_values() -> None:
    snapshot = ControlCenterSnapshot()
    assert snapshot.telemetry == TelemetrySnapshot()
    assert snapshot.memory.total == 0
    assert snapshot.skills.total == 0
    assert snapshot.vision.enabled is False


def test_control_center_bus_publishes_and_replays() -> None:
    bus = ControlCenterBus()
    seen: list[ControlCenterSnapshot] = []

    bus.update_telemetry(TelemetrySnapshot(sequence=1, turn_ms=100))
    bus.update_memory(MemorySummary(total=5, by_sensitivity={}, with_consent=3, expired=0))
    bus.update_skills(SkillSummary(total=2, total_executions=10, total_successes=8))
    bus.update_vision(VisionSummary(enabled=True))

    seen = []
    bus.subscribe(seen.append)

    assert len(seen) == 1
    received = seen[0]
    assert received.telemetry.sequence == 1
    assert received.telemetry.turn_ms == 100
    assert received.memory.total == 5
    assert received.skills.total == 2
    assert received.vision.enabled is True


def test_control_center_bus_subscribe_replay_current() -> None:
    bus = ControlCenterBus()
    bus.update_telemetry(TelemetrySnapshot(sequence=42))

    seen = []
    bus.subscribe(seen.append)

    assert len(seen) == 1
    received = seen[0]
    assert received.telemetry.sequence == 42


def test_control_center_bus_isolation_between_updates() -> None:
    bus = ControlCenterBus()
    bus.update_telemetry(TelemetrySnapshot(sequence=1))

    mem_snapshot = MemorySummary(total=1, by_sensitivity={}, with_consent=1, expired=0)
    bus.update_memory(mem_snapshot)

    snap = bus.snapshot
    assert snap.telemetry.sequence == 1
    assert snap.memory.total == 1


def test_screen_change_enum_values() -> None:
    assert ScreenChange.BASELINE == "baseline"
    assert ScreenChange.CHANGED == "changed"
    assert ScreenChange.UNCHANGED == "unchanged"


def test_memory_summary_with_entries() -> None:
    now = datetime.now(timezone.utc)
    entries = [
        MemoryEntry(content="Teste 1", category="test", sensitivity=MemorySensitivity.PUBLIC),
        MemoryEntry(content="Teste 2", category="test", sensitivity=MemorySensitivity.PRIVATE),
        MemoryEntry(content="Teste 3", category="test", sensitivity=MemorySensitivity.SENSITIVE, retention_days=7),
    ]

    summary = MemorySummary(
        total=3,
        by_sensitivity={"public": 1, "private": 1, "sensitive": 1},
        with_consent=3,
        expired=0,
        recent=entries,
    )

    assert summary.total == 3
    assert summary.by_sensitivity["public"] == 1
    assert summary.with_consent == 3


def test_skill_summary_with_definitions() -> None:
    skill = SkillDefinition(
        name="skill-teste",
        steps=(SkillStep(tool_name="tool1", arguments={}),),
        execution_count=5,
        success_count=4,
    )

    summary = SkillSummary(
        total=1,
        total_executions=5,
        total_successes=4,
        recent=[skill],
    )

    assert summary.total == 1
    assert summary.total_executions == 5
    assert summary.total_successes == 4
    assert summary.recent[0].name == "skill-teste"