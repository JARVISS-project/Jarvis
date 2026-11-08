from datetime import datetime, timedelta, timezone

from jarvis.context.pending_action import (
    PendingAction,
    PendingActionContext,
    PendingActionState,
)


def test_pending_action_expires_and_is_cleared() -> None:
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    current = [now]
    context = PendingActionContext(30, clock=lambda: current[0])
    context.set(
        PendingAction("delete_file", "apague", "path", created_at=now)
    )
    assert context.has_pending()
    current[0] = now + timedelta(seconds=31)
    assert context.get() is None
    assert not context.has_pending()


def test_pending_action_can_be_reset() -> None:
    context = PendingActionContext()
    context.set(PendingAction("delete_file", "apague", "path"))
    context.clear()
    assert not context.has_pending()


def test_pending_action_has_explicit_states_and_cancel_removes_it() -> None:
    context = PendingActionContext()
    context.set(PendingAction("delete_file", "apague", "path"))
    pending = context.transition(
        PendingActionState.WAITING_TARGET_CONFIRMATION
    )
    assert pending is not None
    assert pending.state is PendingActionState.WAITING_TARGET_CONFIRMATION
    context.cancel()
    assert context.get() is None
