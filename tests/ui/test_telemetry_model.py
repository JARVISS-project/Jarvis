from jarvis.application.telemetry import TelemetrySnapshot
from jarvis.ui.telemetry_model import format_telemetry


def test_unknown_telemetry_does_not_claim_success() -> None:
    display = format_telemetry(TelemetrySnapshot())

    assert display.channels == "TURN --   LLM --"
    assert display.health == "TOOLS --   AUDIT --"


def test_completed_telemetry_formats_only_bounded_scalars() -> None:
    display = format_telemetry(
        TelemetrySnapshot(
            has_completed_turn=True,
            turn_ms=842,
            llm_ms=731,
            llm_calls=1,
            tool_ms=4,
            tool_calls=1,
            audit_events=17,
            audit_write_ok=True,
        )
    )

    assert display.channels == "TURN 842ms   LLM 731ms/1"
    assert display.health == "TOOLS 4ms/1   AUDIT OK / 17"
