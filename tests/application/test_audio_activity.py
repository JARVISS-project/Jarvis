from jarvis.application.audio_activity import AudioActivityBus


def test_audio_activity_bus_clamps_and_replays_latest_snapshot() -> None:
    bus = AudioActivityBus()
    seen = []

    bus.publish(1.5, peak=-2.0)
    unsubscribe = bus.subscribe(seen.append)

    assert seen[-1].level == 1.0
    assert seen[-1].peak == 1.0
    unsubscribe()


def test_audio_activity_bus_isolates_subscriber_failures() -> None:
    bus = AudioActivityBus()
    seen = []

    def broken(_snapshot) -> None:
        raise RuntimeError("boom")

    bus.subscribe(broken, replay_current=False)
    bus.subscribe(seen.append, replay_current=False)

    bus.publish(0.35)

    assert seen[-1].level == 0.35
