import json
from pathlib import Path
import time

import pytest

from jarvis.application.audio_activity import AudioActivityBus
from jarvis.application.conversation_log import ConversationLogBus
from jarvis.application.state import AgentState, AgentStateBus, StateSnapshot
from jarvis.application.telemetry import TelemetryBus, TelemetrySnapshot
from jarvis.application.state_ipc import (
    LocalStateClient,
    LocalStateServer,
    StateEndpoint,
)


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Condição não ocorreu dentro do timeout.")


def test_state_endpoint_round_trip_and_cleanup(tmp_path: Path) -> None:
    endpoint_path = tmp_path / "runtime" / "ambient-ui.json"
    server = LocalStateServer(
        AgentStateBus(),
        endpoint_path=endpoint_path,
    )

    endpoint = server.start()

    assert endpoint.host == "127.0.0.1"
    assert endpoint.port > 0
    assert StateEndpoint.read(endpoint_path) == endpoint

    server.close()

    assert endpoint_path.exists() is False


def test_state_ipc_replicates_runtime_state(tmp_path: Path) -> None:
    runtime_bus = AgentStateBus()
    ui_bus = AgentStateBus()
    server = LocalStateServer(
        runtime_bus,
        endpoint_path=tmp_path / "ambient-ui.json",
    )
    endpoint = server.start()
    client = LocalStateClient(ui_bus, endpoint, reconnect_seconds=0.05)
    client.start()
    try:
        runtime_bus.publish(AgentState.PROCESSING, source="voice_pipeline")
        _wait_until(lambda: ui_bus.snapshot.state is AgentState.PROCESSING)

        assert ui_bus.snapshot.source == "ipc:voice_pipeline"
    finally:
        client.close()
        server.close()


def test_state_ipc_does_not_serialize_snapshot_detail() -> None:
    snapshot = StateSnapshot(
        state=AgentState.EXECUTING,
        previous_state=AgentState.PROCESSING,
        sequence=8,
        source="tool_executor",
        detail="nota-secreta.md",
    )

    payload = json.loads(LocalStateServer._encode(snapshot).decode("utf-8"))

    assert payload["state"] == "executing"
    assert payload["source"] == "tool_executor"
    assert "detail" not in payload


def test_state_endpoint_rejects_non_loopback_host() -> None:
    with pytest.raises(ValueError, match="loopback"):
        StateEndpoint.from_dict(
            {
                "host": "0.0.0.0",
                "port": 12345,
                "pid": 1,
                "protocol_version": 1,
            }
        )


def test_telemetry_ipc_replicates_only_aggregate_metrics(tmp_path: Path) -> None:
    runtime_state = AgentStateBus()
    runtime_telemetry = TelemetryBus()
    ui_state = AgentStateBus()
    ui_telemetry = TelemetryBus()
    server = LocalStateServer(
        runtime_state,
        endpoint_path=tmp_path / "ambient-ui.json",
        telemetry_bus=runtime_telemetry,
    )
    endpoint = server.start()
    client = LocalStateClient(
        ui_state,
        endpoint,
        reconnect_seconds=0.05,
        telemetry_bus=ui_telemetry,
    )
    client.start()
    try:
        runtime_telemetry.publish_turn(
            turn_ms=12,
            llm_ms=5,
            llm_calls=1,
            tool_ms=2,
            tool_calls=1,
        )
        runtime_telemetry.record_audit(True)
        _wait_until(lambda: ui_telemetry.snapshot.audit_events == 1)

        assert ui_telemetry.snapshot.tool_calls == 1
        assert ui_state.snapshot.state is AgentState.WAITING_WAKE
    finally:
        client.close()
        server.close()


def test_telemetry_encoding_has_fixed_privacy_allowlist() -> None:
    payload = json.loads(
        LocalStateServer._encode_telemetry(
            TelemetrySnapshot(
                sequence=2,
                has_completed_turn=True,
                turn_ms=10,
                llm_ms=4,
                llm_calls=1,
                tool_ms=2,
                tool_calls=1,
                audit_events=3,
                audit_write_ok=True,
            )
        ).decode("utf-8")
    )

    assert set(payload) == {
        "protocol_version",
        "type",
        "sequence",
        "has_completed_turn",
        "turn_ms",
        "llm_ms",
        "llm_calls",
        "tool_ms",
        "tool_calls",
        "audit_events",
        "audit_write_ok",
    }
    encoded = json.dumps(payload)
    for secret in ("target", "arguments", "pixels", "digest", "tool_name"):
        assert secret not in encoded


def test_invalid_telemetry_payload_is_ignored() -> None:
    telemetry = TelemetryBus()
    client = LocalStateClient(
        AgentStateBus(),
        StateEndpoint("127.0.0.1", 12345, 1),
        telemetry_bus=telemetry,
    )
    payload = {
        "protocol_version": 1,
        "type": "telemetry",
        "sequence": 1,
        "has_completed_turn": True,
        "turn_ms": True,
        "llm_ms": 0,
        "llm_calls": 0,
        "tool_ms": 0,
        "tool_calls": 0,
        "audit_events": 0,
        "audit_write_ok": None,
    }

    client._handle_line(json.dumps(payload).encode("utf-8"))

    assert telemetry.snapshot == TelemetrySnapshot()


def test_ipc_publication_only_enqueues_and_never_waits_for_clients(
    tmp_path: Path,
) -> None:
    server = LocalStateServer(
        AgentStateBus(),
        endpoint_path=tmp_path / "ambient-ui.json",
        telemetry_bus=TelemetryBus(),
    )
    started = time.perf_counter()

    server._broadcast_telemetry(TelemetrySnapshot(sequence=1))

    assert time.perf_counter() - started < 0.05


def test_client_ignores_stale_runtime_state_sequence() -> None:
    state_bus = AgentStateBus()
    client = LocalStateClient(
        state_bus,
        StateEndpoint("127.0.0.1", 12345, 1),
    )
    current = {
        "protocol_version": 1,
        "type": "state",
        "state": "error",
        "sequence": 5,
        "source": "runtime",
    }
    stale = {
        "protocol_version": 1,
        "type": "state",
        "state": "processing",
        "sequence": 4,
        "source": "runtime",
    }

    client._handle_line(json.dumps(current).encode("utf-8"))
    client._handle_line(json.dumps(stale).encode("utf-8"))

    assert state_bus.snapshot.state is AgentState.ERROR


def test_audio_activity_ipc_replicates_only_normalized_scalars(
    tmp_path: Path,
) -> None:
    runtime_state = AgentStateBus()
    runtime_audio = AudioActivityBus()
    ui_state = AgentStateBus()
    ui_audio = AudioActivityBus()
    server = LocalStateServer(
        runtime_state,
        endpoint_path=tmp_path / "ambient-ui.json",
        audio_activity_bus=runtime_audio,
    )
    endpoint = server.start()
    client = LocalStateClient(
        ui_state,
        endpoint,
        reconnect_seconds=0.05,
        audio_activity_bus=ui_audio,
    )
    client.start()
    try:
        runtime_audio.publish(0.72, peak=0.84)
        _wait_until(lambda: ui_audio.snapshot.level == pytest.approx(0.72))

        assert ui_audio.snapshot.peak == pytest.approx(0.84)
        assert ui_state.snapshot.state is AgentState.WAITING_WAKE
    finally:
        client.close()
        server.close()


def test_audio_activity_encoding_contains_no_pcm_or_transcript() -> None:
    bus = AudioActivityBus()
    bus.publish(0.4, peak=0.7)

    payload = json.loads(
        LocalStateServer._encode_audio_activity(bus.snapshot).decode("utf-8")
    )

    assert set(payload) == {
        "protocol_version",
        "type",
        "sequence",
        "level",
        "peak",
    }
    encoded = json.dumps(payload)
    for forbidden in ("pcm", "samples", "text", "transcript"):
        assert forbidden not in encoded.lower()


def test_conversation_log_ipc_replicates_recent_turns(tmp_path: Path) -> None:
    runtime_state = AgentStateBus()
    runtime_log = ConversationLogBus()
    ui_state = AgentStateBus()
    ui_log = ConversationLogBus()
    server = LocalStateServer(
        runtime_state,
        endpoint_path=tmp_path / "ambient-ui.json",
        conversation_log_bus=runtime_log,
    )
    endpoint = server.start()
    client = LocalStateClient(
        ui_state,
        endpoint,
        reconnect_seconds=0.05,
        conversation_log_bus=ui_log,
    )
    client.start()
    try:
        runtime_log.add_user("Que horas são?")
        runtime_log.add_assistant("Agora são 14:32.")
        _wait_until(lambda: ui_log.snapshot.sequence >= 2)

        assert [(e.role, e.text) for e in ui_log.snapshot.entries] == [
            ("user", "Que horas são?"),
            ("assistant", "Agora são 14:32."),
        ]
    finally:
        client.close()
        server.close()


def test_conversation_log_encoding_is_bounded() -> None:
    bus = ConversationLogBus(max_entries=20)
    for index in range(15):
        bus.add_user(f"mensagem-{index}-" + ("x" * 2500))

    payload = json.loads(
        LocalStateServer._encode_conversation_log(bus.snapshot).decode("utf-8")
    )

    assert payload["type"] == "conversation_log"
    assert len(payload["entries"]) == 12
    assert all(len(entry["text"]) <= 2000 for entry in payload["entries"])
