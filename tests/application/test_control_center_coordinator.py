"""Testes para o coordenador do Control Center."""

import pytest

from jarvis.application.control_center_coordinator import ControlCenterCoordinator
from jarvis.application.telemetry import TelemetryBus, TelemetrySnapshot
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.memory.models import MemoryEntry, MemorySensitivity
from jarvis.skills.store import InMemorySkillStore
from jarvis.skills.models import SkillDefinition, SkillStep
from jarvis.ui.control_center_model import ControlCenterBus, VisionSummary
from jarvis.vision.service import VisionService


class FakeVisionService:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._last_obs = None

    def observe_change(self):
        from jarvis.vision.models import ScreenChangeResult, ScreenChange
        self._last_obs = ScreenChangeResult(ScreenChange.CHANGED, 100, 100)
        return self._last_obs


def test_control_center_coordinator_refreshes_memory() -> None:
    memory_store = InMemoryMemoryStore()
    memory_store.add("Memória 1", "cat1", sensitivity="public")
    memory_store.add("Memória 2", "cat2", sensitivity="private", retention_days=30)

    skill_store = InMemorySkillStore()
    telemetry_bus = TelemetryBus()
    control_center_bus = ControlCenterBus()
    vision_service = FakeVisionService()

    coordinator = ControlCenterCoordinator(
        memory_store, skill_store, vision_service, telemetry_bus, control_center_bus
    )

    coordinator.refresh_memory()

    snapshot = control_center_bus.snapshot
    assert snapshot.memory.total == 2
    assert snapshot.memory.by_sensitivity["public"] == 1
    assert snapshot.memory.by_sensitivity["private"] == 1
    assert snapshot.memory.with_consent == 2
    assert len(snapshot.memory.recent) == 2


def test_control_center_coordinator_refreshes_skills() -> None:
    skill_store = InMemorySkillStore()
    skill = SkillDefinition(
        name="skill-teste",
        steps=(SkillStep(tool_name="tool1", arguments={}),),
        execution_count=5,
        success_count=4,
        triggers=("trigger1", "trigger2"),
    )
    skill_store.save(skill)

    memory_store = InMemoryMemoryStore()
    telemetry_bus = TelemetryBus()
    control_center_bus = ControlCenterBus()
    vision_service = FakeVisionService()

    coordinator = ControlCenterCoordinator(
        memory_store, skill_store, vision_service, telemetry_bus, control_center_bus
    )

    coordinator.refresh_skills()

    snapshot = control_center_bus.snapshot
    assert snapshot.skills.total == 1
    assert snapshot.skills.total_executions == 5
    assert snapshot.skills.total_successes == 4
    assert len(snapshot.skills.recent) == 1
    assert snapshot.skills.recent[0].name == "skill-teste"


def test_control_center_coordinator_refreshes_vision_enabled() -> None:
    memory_store = InMemoryMemoryStore()
    skill_store = InMemorySkillStore()
    telemetry_bus = TelemetryBus()
    control_center_bus = ControlCenterBus()
    vision_service = FakeVisionService(enabled=True)

    coordinator = ControlCenterCoordinator(
        memory_store, skill_store, vision_service, telemetry_bus, control_center_bus
    )

    coordinator.refresh_vision()

    snapshot = control_center_bus.snapshot
    assert snapshot.vision.enabled is True


def test_control_center_coordinator_refreshes_vision_disabled() -> None:
    memory_store = InMemoryMemoryStore()
    skill_store = InMemorySkillStore()
    telemetry_bus = TelemetryBus()
    control_center_bus = ControlCenterBus()

    coordinator = ControlCenterCoordinator(
        memory_store, skill_store, None, telemetry_bus, control_center_bus
    )

    coordinator.refresh_vision()

    snapshot = control_center_bus.snapshot
    assert snapshot.vision.enabled is False


def test_control_center_coordinator_updates_on_telemetry() -> None:
    memory_store = InMemoryMemoryStore()
    skill_store = InMemorySkillStore()
    telemetry_bus = TelemetryBus()
    control_center_bus = ControlCenterBus()
    vision_service = FakeVisionService()

    coordinator = ControlCenterCoordinator(
        memory_store, skill_store, FakeVisionService(), telemetry_bus, control_center_bus
    )

    telemetry_bus.publish_turn(
        turn_ms=500,
        llm_ms=400,
        llm_calls=1,
        tool_ms=20,
        tool_calls=1,
    )

    snapshot = control_center_bus.snapshot
    assert snapshot.telemetry.turn_ms == 500
    assert snapshot.telemetry.llm_ms == 400
    assert snapshot.telemetry.tool_calls == 1


def test_control_center_coordinator_close_unsubscribes() -> None:
    memory_store = InMemoryMemoryStore()
    skill_store = InMemorySkillStore()
    telemetry_bus = TelemetryBus()
    control_center_bus = ControlCenterBus()

    coordinator = ControlCenterCoordinator(
        memory_store, skill_store, FakeVisionService(), telemetry_bus, control_center_bus
    )

    coordinator.close()

    # Should not raise
    telemetry_bus.publish_turn(turn_ms=100, llm_ms=0, llm_calls=0, tool_ms=0, tool_calls=0)