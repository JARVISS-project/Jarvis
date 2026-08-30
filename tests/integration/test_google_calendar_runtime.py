from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis.__main__ import build_harness
from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.integrations.google.calendar.models import CalendarAvailability
from jarvis.integrations.google.calendar.tools import (
    GoogleCalendarCheckAvailabilityTool,
    GoogleCalendarListEventsTool,
)
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.tools.registry import ToolRegistry


FIXED_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


class NeverLLM:
    calls = 0

    def generate(self, **_kwargs):
        self.calls += 1
        raise AssertionError("Calendar fast path must not call the LLM")


class FakeProvider:
    def get_timezone(self, _calendar_id):
        return timezone.utc

    def list_events(self, _calendar_id, _period, _max_results=50):
        return []

    def get_event(self, _calendar_id, _event_id):
        return None

    def check_availability(self, _calendar_id, _start, _end):
        return CalendarAvailability(False, 0)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, tool, **kwargs):
        self.calls.append((tool.name, dict(kwargs)))
        return tool.execute(**kwargs)


def test_builder_registers_calendar_only_when_capability_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("JARVIS_GOOGLE_CALENDAR", raising=False)
    common = {
        "settings_path": tmp_path / "missing-settings.json",
        "database_path": tmp_path / "jarvis.db",
        "audit_log_path": tmp_path / "audit.log",
        "skill_store_path": tmp_path / "skills.json",
        "filesystem_read_enabled": False,
        "filesystem_write_enabled": False,
        "obsidian_enabled": False,
    }
    disabled = build_harness(
        JarvisConfig(google_calendar_enabled=False, **common),
        ResourceManager(),
    )
    enabled = build_harness(
        JarvisConfig(google_calendar_enabled=True, **common),
        ResourceManager(),
    )
    try:
        assert disabled.tool_registry.get("google_calendar_list_events") is None
        assert enabled.tool_registry.get("google_calendar_list_events") is not None
        assert enabled.tool_registry.get("google_calendar_check_availability") is not None
    finally:
        disabled.close_confirmation_sessions()
        enabled.close_confirmation_sessions()


def test_calendar_list_and_availability_fast_paths_skip_llm() -> None:
    provider = FakeProvider()
    registry = ToolRegistry()
    registry.register(
        GoogleCalendarListEventsTool(
            provider, timezone.utc, clock=lambda: FIXED_NOW
        )
    )
    registry.register(
        GoogleCalendarCheckAvailabilityTool(
            provider, timezone.utc, clock=lambda: FIXED_NOW
        )
    )
    executor = RecordingExecutor()
    llm = NeverLLM()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=executor,
        memory=ConversationMemory(),
    )

    assert harness.processar_mensagem("O que eu tenho amanhã?") == (
        "Nenhum evento encontrado para amanhã."
    )
    assert harness.processar_mensagem("Estou livre amanhã das 9 às 10?") == (
        "Livre no período consultado."
    )
    assert llm.calls == 0
    assert harness.last_tool_trace == []
    assert executor.calls == [
        (
            "google_calendar_list_events",
            {"period": "tomorrow", "calendar_id": "primary"},
        ),
        (
            "google_calendar_check_availability",
            {
                "period": "tomorrow",
                "calendar_id": "primary",
                "start_time": "09:00",
                "end_time": "10:00",
            },
        ),
    ]
