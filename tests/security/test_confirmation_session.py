from threading import Barrier, Thread

from jarvis.security.confirmation_session import (
    ConfirmationDecision,
    ConfirmationEventKind,
    ConfirmationSessionManager,
    ConfirmationSessionState,
)
from jarvis.security.risk import RiskLevel


def request(manager: ConfirmationSessionManager):  # type: ignore[no-untyped-def]
    return manager.request(
        tool_name="critical_tool",
        target="target:a",
        arguments={"target": "a"},
        risk=RiskLevel.CRITICAL,
        interaction_id="interaction-a",
        prompt_text="Posso continuar?",
    )


def test_session_transitions_and_binding() -> None:
    now = [0.0]
    manager = ConfirmationSessionManager(
        clock=lambda: now[0],
        start_scheduler=False,
    )
    session = request(manager)
    assert session is not None
    assert session.state is ConfirmationSessionState.WAITING_CONFIRMATION

    stale, _session, _grant = manager.decide(
        confirmed=True,
        denied=False,
        session_id="old-session",
        challenge_id=session.challenge_id,
        grant_factory=lambda _session: object(),
    )
    assert stale is ConfirmationDecision.STALE
    assert manager.current() is not None

    decision, terminal, grant = manager.decide(
        confirmed=True,
        denied=False,
        session_id=session.id,
        challenge_id=session.challenge_id,
        grant_factory=lambda _session: "grant",
    )
    assert decision is ConfirmationDecision.CONFIRMED
    assert terminal is not None
    assert terminal.state is ConfirmationSessionState.CONFIRMED
    assert grant == "grant"
    assert manager.current() is None


def test_denial_and_expiration_run_cleanup() -> None:
    now = [0.0]
    cleaned: list[str] = []
    manager = ConfirmationSessionManager(
        timeout_seconds=120.0,
        clock=lambda: now[0],
        cleanup=lambda session: cleaned.append(session.id),
        start_scheduler=False,
    )
    denied_session = request(manager)
    assert denied_session is not None
    decision, _terminal, _grant = manager.decide(
        confirmed=False,
        denied=True,
        session_id=denied_session.id,
        challenge_id=denied_session.challenge_id,
        grant_factory=lambda _session: None,
    )
    assert decision is ConfirmationDecision.DENIED
    assert cleaned == [denied_session.id]

    expired_session = request(manager)
    assert expired_session is not None
    now[0] = 120.0
    manager.process_due()
    assert manager.current() is None
    assert manager.state() is ConfirmationSessionState.EXPIRED
    assert cleaned == [denied_session.id, expired_session.id]
    event = manager.pop_event()
    assert event is not None
    assert event.kind is ConfirmationEventKind.EXPIRED


def test_reminders_are_bounded_and_deterministic() -> None:
    now = [0.0]
    manager = ConfirmationSessionManager(
        first_reminder_seconds=25.0,
        reminder_interval_seconds=30.0,
        max_reminders=2,
        timeout_seconds=120.0,
        clock=lambda: now[0],
        start_scheduler=False,
    )
    assert request(manager) is not None

    now[0] = 24.9
    manager.process_due()
    assert manager.pop_event() is None
    now[0] = 25.0
    manager.process_due()
    first = manager.pop_event()
    assert first is not None
    assert first.kind is ConfirmationEventKind.REMINDER
    assert first.reminder_index == 1
    now[0] = 55.0
    manager.process_due()
    second = manager.pop_event()
    assert second is not None
    assert second.reminder_index == 2
    now[0] = 100.0
    manager.process_due()
    assert manager.pop_event() is None


def test_late_response_cannot_authorize_replacement_session() -> None:
    manager = ConfirmationSessionManager(start_scheduler=False)
    first = request(manager)
    assert first is not None
    assert manager.cancel()
    second = request(manager)
    assert second is not None

    decision, _session, _grant = manager.decide(
        confirmed=True,
        denied=False,
        session_id=first.id,
        challenge_id=first.challenge_id,
        grant_factory=lambda _session: "grant",
    )
    assert decision is ConfirmationDecision.STALE
    assert manager.current() == second


def test_confirmation_and_expiration_are_single_winner() -> None:
    now = [0.0]
    manager = ConfirmationSessionManager(
        timeout_seconds=120.0,
        clock=lambda: now[0],
        start_scheduler=False,
    )
    session = request(manager)
    assert session is not None
    barrier = Barrier(3)
    decisions: list[ConfirmationDecision] = []

    def confirm() -> None:
        barrier.wait()
        decision, _session, _grant = manager.decide(
            confirmed=True,
            denied=False,
            session_id=session.id,
            challenge_id=session.challenge_id,
            grant_factory=lambda _session: "grant",
        )
        decisions.append(decision)

    def expire() -> None:
        barrier.wait()
        now[0] = 120.0
        manager.process_due()

    threads = [Thread(target=confirm), Thread(target=expire)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert manager.current() is None
    assert manager.state() in {
        ConfirmationSessionState.CONFIRMED,
        ConfirmationSessionState.EXPIRED,
    }
    assert decisions in (
        [ConfirmationDecision.CONFIRMED],
        [ConfirmationDecision.STALE],
    )
