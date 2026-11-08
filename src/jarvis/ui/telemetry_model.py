"""Formatação pura da telemetria agregada exibida pela Ambient HUD."""

from dataclasses import dataclass

from jarvis.application.telemetry import TelemetrySnapshot


@dataclass(frozen=True, slots=True)
class TelemetryDisplay:
    channels: str
    health: str


def format_telemetry(snapshot: TelemetrySnapshot) -> TelemetryDisplay:
    if snapshot.has_completed_turn:
        channels = (
            f"TURN {snapshot.turn_ms}ms   "
            f"LLM {snapshot.llm_ms}ms/{snapshot.llm_calls}"
        )
    else:
        channels = "TURN --   LLM --"

    if snapshot.audit_write_ok is True:
        audit = f"AUDIT OK / {snapshot.audit_events}"
    elif snapshot.audit_write_ok is False:
        audit = f"AUDIT ERROR / {snapshot.audit_events}"
    else:
        audit = "AUDIT --"
    tools = (
        f"TOOLS {snapshot.tool_ms}ms/{snapshot.tool_calls}"
        if snapshot.has_completed_turn
        else "TOOLS --"
    )
    return TelemetryDisplay(channels, f"{tools}   {audit}")
