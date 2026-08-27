import json
from pathlib import Path
import time

import pytest

from jarvis.application.state import AgentState, AgentStateBus, StateSnapshot
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
