"""Coordenador do Control Center - agrega dados e publica no bus."""

from collections.abc import Callable
from datetime import datetime, timezone

from jarvis.application.telemetry import TelemetryBus, TelemetrySnapshot
from jarvis.memory.store import MemoryStore
from jarvis.skills.store import SkillStore
from jarvis.ui.control_center_model import (
    ControlCenterBus,
    ControlCenterSnapshot,
    MemorySummary,
    ScreenChange,
    SkillSummary,
    VisionSummary,
)
from jarvis.vision.service import VisionService


class ControlCenterCoordinator:
    """Agrega dados de memória, skills, visão e telemetria para o Control Center."""

    def __init__(
        self,
        memory_store: MemoryStore,
        skill_store: SkillStore,
        vision_service: VisionService | None,
        telemetry_bus: TelemetryBus,
        control_center_bus: ControlCenterBus,
    ) -> None:
        self.memory_store = memory_store
        self.skill_store = skill_store
        self.vision_service = vision_service
        self.telemetry_bus = telemetry_bus
        self.control_center_bus = control_center_bus

        # Subscribe to telemetry updates
        self._unsubscribe_telemetry = telemetry_bus.subscribe(self._on_telemetry)

    def _on_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self.control_center_bus.update_telemetry(snapshot)

    def refresh_memory(self) -> None:
        """Publica resumo atualizado da memória."""
        all_memories = self.memory_store.get_all()
        by_sensitivity: dict[str, int] = {}
        with_consent = 0
        expired = 0
        for mem in all_memories:
            sens = mem.sensitivity.value
            by_sensitivity[sens] = by_sensitivity.get(sens, 0) + 1
            if mem.consent_given:
                with_consent += 1
            if mem.is_expired():
                expired += 1

        # Recent memories (last 10, sorted by created_at desc)
        recent = sorted(all_memories, key=lambda m: m.created_at, reverse=True)[:10]

        summary = MemorySummary(
            total=len(all_memories),
            by_sensitivity=by_sensitivity,
            with_consent=with_consent,
            expired=expired,
            recent=recent,
        )
        self.control_center_bus.update_memory(summary)

    def refresh_skills(self) -> None:
        """Publica resumo atualizado das skills."""
        all_skills = self.skill_store.list()
        total_executions = sum(s.execution_count for s in all_skills)
        total_successes = sum(s.success_count for s in all_skills)
        recent = sorted(all_skills, key=lambda s: s.last_used_at or "", reverse=True)[:10]

        summary = SkillSummary(
            total=len(all_skills),
            total_executions=total_executions,
            total_successes=total_successes,
            recent=recent,
        )
        self.control_center_bus.update_skills(summary)

    def refresh_vision(self) -> None:
        """Publica resumo atualizado da visão."""
        if self.vision_service is None:
            summary = VisionSummary(enabled=False)
            self.control_center_bus.update_vision(summary)
            return

        # Get last observation from vision service
        last_obs = None
        # Note: VisionService doesn't expose last observation directly
        # In a real implementation, we'd track this. For now, just report enabled.
        summary = VisionSummary(enabled=True)
        self.control_center_bus.update_vision(summary)

    def refresh_all(self) -> None:
        """Atualiza todos os resumos."""
        self.refresh_memory()
        self.refresh_skills()
        self.refresh_vision()

    def close(self) -> None:
        if self._unsubscribe_telemetry is not None:
            self._unsubscribe_telemetry()