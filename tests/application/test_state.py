from jarvis.application.state import AgentState, AgentStateBus


def test_state_bus_replays_current_state_and_publishes_transition() -> None:
    bus = AgentStateBus()
    snapshots = []

    unsubscribe = bus.subscribe(snapshots.append)
    published = bus.publish(
        AgentState.PROCESSING,
        source="test",
        detail="analisando",
    )

    assert [snapshot.state for snapshot in snapshots] == [
        AgentState.WAITING_WAKE,
        AgentState.PROCESSING,
    ]
    assert published.previous_state is AgentState.WAITING_WAKE
    assert published.sequence == 1
    assert published.source == "test"
    assert published.detail == "analisando"

    unsubscribe()
    bus.publish(AgentState.SPEAKING, source="test")
    assert len(snapshots) == 2


def test_state_bus_isolates_broken_subscriber_from_core() -> None:
    bus = AgentStateBus()
    received = []

    def broken(_snapshot: object) -> None:
        raise RuntimeError("UI falhou")

    bus.subscribe(broken, replay_current=False)
    bus.subscribe(received.append, replay_current=False)

    snapshot = bus.publish(AgentState.LISTENING, source="test")

    assert snapshot.state is AgentState.LISTENING
    assert [item.state for item in received] == [AgentState.LISTENING]
