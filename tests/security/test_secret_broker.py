from datetime import datetime, timedelta, timezone

import pytest

from jarvis.security.secret_broker import SecretBroker


def test_secret_reference_never_reveals_plaintext() -> None:
    broker = SecretBroker()
    reference = broker.register("google-refresh", "super-secret-value")

    rendered = f"{reference!r}\n{reference}"

    assert "super-secret-value" not in rendered
    assert "secret://" in rendered


def test_capability_is_purpose_bound_and_one_use() -> None:
    broker = SecretBroker()
    reference = broker.register("api", "token-123")
    capability = broker.issue_capability(reference, purpose="calendar-read")

    with pytest.raises(PermissionError):
        broker.lease(reference, capability, purpose="calendar-write")

    capability = broker.issue_capability(reference, purpose="calendar-read")
    with broker.lease(
        reference,
        capability,
        purpose="calendar-read",
    ) as value:
        assert value == "token-123"

    with pytest.raises(PermissionError):
        broker.lease(reference, capability, purpose="calendar-read")


def test_expired_capability_fails_closed() -> None:
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    current = [now]
    broker = SecretBroker(
        capability_lifetime_seconds=5,
        clock=lambda: current[0],
    )
    reference = broker.register("api", "token-123")
    capability = broker.issue_capability(reference, purpose="call")

    current[0] += timedelta(seconds=6)

    with pytest.raises(PermissionError):
        broker.lease(reference, capability, purpose="call")


def test_revoke_invalidates_secret_and_capabilities() -> None:
    broker = SecretBroker()
    reference = broker.register("api", "token-123")
    capability = broker.issue_capability(reference, purpose="call")

    broker.revoke(reference)

    with pytest.raises(PermissionError):
        broker.lease(reference, capability, purpose="call")
    with pytest.raises(KeyError):
        broker.issue_capability(reference, purpose="call")


def test_audit_receives_only_reference_metadata() -> None:
    events: list[tuple[str, str, str]] = []
    broker = SecretBroker(audit_sink=lambda *event: events.append(event))
    secret = "plaintext-that-must-never-be-logged"
    reference = broker.register("api", secret)
    capability = broker.issue_capability(reference, purpose="call")

    with broker.lease(reference, capability, purpose="call"):
        pass

    serialized = "\n".join("|".join(event) for event in events)
    assert secret not in serialized
    assert reference.id in serialized
    assert any("opened" in event for event in events)
    assert any("closed" in event for event in events)


def test_reference_is_safe_for_markdown_handoff() -> None:
    broker = SecretBroker()
    secret = "markdown-secret"
    reference = broker.register("worker-secret", secret)

    handoff = f"- credential: {reference}\n- label: {reference.label}"

    assert secret not in handoff
    assert "secret://" in handoff
