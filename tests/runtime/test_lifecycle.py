from jarvis.runtime.lifecycle import RuntimeLifecycle, RuntimeState


def test_lifecycle_rolls_back_in_reverse_order_after_partial_startup() -> None:
    events: list[str] = []
    lifecycle = RuntimeLifecycle(lambda state: events.append(state.value))

    lifecycle.begin_startup()
    lifecycle.add_cleanup(lambda: events.append("first"))
    lifecycle.add_cleanup(lambda: events.append("second"))
    errors = lifecycle.stop()

    assert errors == ()
    assert events == ["STARTING", "STOPPING", "second", "first", "STOPPED"]


def test_lifecycle_cleanup_is_idempotent_and_continues_after_failure() -> None:
    calls: list[str] = []
    lifecycle = RuntimeLifecycle()
    lifecycle.begin_startup()
    lifecycle.add_cleanup(lambda: calls.append("safe"))

    def fail() -> None:
        calls.append("fail")
        raise RuntimeError("cleanup")

    lifecycle.add_cleanup(fail)
    lifecycle.mark_running()

    errors = lifecycle.stop()
    assert len(errors) == 1
    assert calls == ["fail", "safe"]
    assert lifecycle.state is RuntimeState.STOPPED
    assert lifecycle.stop() == ()


def test_observability_failure_never_prevents_cleanup() -> None:
    cleaned: list[bool] = []

    def fail_to_publish(_state: RuntimeState) -> None:
        raise OSError("disk unavailable")

    lifecycle = RuntimeLifecycle(fail_to_publish)
    lifecycle.begin_startup()
    lifecycle.add_cleanup(lambda: cleaned.append(True))

    assert lifecycle.stop() == ()
    assert cleaned == [True]
