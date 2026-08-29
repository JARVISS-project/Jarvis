from pathlib import Path

from jarvis.runtime.health import RuntimeHealthStore
from jarvis.runtime.lifecycle import RuntimeCapabilities, RuntimeState


def test_health_is_local_aggregate_and_stop_is_instance_bound(tmp_path: Path) -> None:
    store = RuntimeHealthStore(tmp_path / "status.json")
    store.publish(
        state=RuntimeState.RUNNING,
        instance_id="one",
        started_at=1.0,
        capabilities=RuntimeCapabilities(voice=True, remote=True),
    )

    current = store.read()
    assert current is not None
    assert current.capabilities == ("voice", "remote")
    assert current.ui_available is False
    assert "token" not in store.status_path.read_text(encoding="utf-8").casefold()

    store.request_stop("other")
    assert store.stop_requested("one") is False
    store.request_stop("one")
    assert store.stop_requested("one") is True
    store.clear_stop_request()
    assert store.stop_requested("one") is False
