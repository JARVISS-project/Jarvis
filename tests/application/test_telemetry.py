import pytest

from jarvis.application.telemetry import (
    MAX_COUNT,
    MAX_DURATION_MS,
    TelemetryBus,
    TelemetrySnapshot,
)


def test_telemetry_bus_publishes_aggregate_turn_and_replays_latest() -> None:
    bus = TelemetryBus()
    bus.publish_turn(
        turn_ms=800,
        llm_ms=700,
        llm_calls=1,
        tool_ms=20,
        tool_calls=2,
    )
    seen = []

    bus.subscribe(seen.append)

    assert seen == [bus.snapshot]
    assert bus.snapshot.has_completed_turn
    assert bus.snapshot.tool_calls == 2


def test_audit_counter_preserves_turn_metrics_and_isolates_subscribers() -> None:
    bus = TelemetryBus()
    bus.publish_turn(
        turn_ms=10,
        llm_ms=0,
        llm_calls=0,
        tool_ms=3,
        tool_calls=1,
    )
    bus.subscribe(lambda _snapshot: (_ for _ in ()).throw(RuntimeError("ui")))

    bus.record_audit(True)

    assert bus.snapshot.audit_events == 1
    assert bus.snapshot.audit_write_ok is True
    assert bus.snapshot.tool_calls == 1


def test_telemetry_rejects_negative_or_non_integer_metrics() -> None:
    bus = TelemetryBus()

    with pytest.raises(ValueError):
        bus.publish_turn(
            turn_ms=-1,
            llm_ms=0,
            llm_calls=0,
            tool_ms=0,
            tool_calls=0,
        )
    with pytest.raises(TypeError):
        bus.publish_turn(
            turn_ms=True,
            llm_ms=0,
            llm_calls=0,
            tool_ms=0,
            tool_calls=0,
        )


def test_telemetry_clamps_large_aggregate_values() -> None:
    bus = TelemetryBus()

    bus.publish_turn(
        turn_ms=MAX_DURATION_MS + 1,
        llm_ms=MAX_DURATION_MS + 1,
        llm_calls=MAX_COUNT + 1,
        tool_ms=MAX_DURATION_MS + 1,
        tool_calls=MAX_COUNT + 1,
    )

    assert bus.snapshot.turn_ms == MAX_DURATION_MS
    assert bus.snapshot.tool_calls == MAX_COUNT


def test_telemetry_ignores_regressive_snapshot_sequence() -> None:
    bus = TelemetryBus()
    bus.publish_snapshot(TelemetrySnapshot(sequence=2, audit_events=2))

    bus.publish_snapshot(TelemetrySnapshot(sequence=1, audit_events=1))

    assert bus.snapshot.sequence == 2
    assert bus.snapshot.audit_events == 2
