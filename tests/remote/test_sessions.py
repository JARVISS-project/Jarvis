from jarvis.remote.sessions import RateLimiter, SessionManager


def test_rate_limiter_uses_bounded_window() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 10, clock=lambda: now[0])

    assert limiter.check("device") is True
    assert limiter.check("device") is True
    assert limiter.check("device") is False
    now[0] = 11.0
    assert limiter.check("device") is True


def test_sessions_expire_and_subscriptions_are_immutable() -> None:
    now = [0.0]
    manager = SessionManager(
        session_timeout_seconds=10,
        clock=lambda: now[0],
    )
    session = manager.create_session("device")
    manager.add_subscription(session.id, "state")

    stored = manager.get_session(session.id)
    assert stored is not None
    assert stored.subscriptions == frozenset({"state"})

    now[0] = 11.0
    assert manager.cleanup_expired() == 1
    assert manager.get_session(session.id) is None


def test_session_limit_removes_oldest_session() -> None:
    now = [0.0]
    manager = SessionManager(max_sessions_per_device=1, clock=lambda: now[0])
    first = manager.create_session("device")
    now[0] = 1.0
    second = manager.create_session("device")

    assert manager.get_session(first.id) is None
    assert manager.get_session(second.id) == second
