from pathlib import Path
import threading

import pytest

from jarvis.integrations.google.auth import GoogleAuthManager
from jarvis.integrations.google.errors import (
    GoogleAuthorizationCancelledError,
    GoogleAuthorizationDeniedError,
    GoogleAuthorizationTimeoutError,
    GoogleOAuthStateError,
    GoogleScopeError,
    GoogleCredentialStorageCorruptError,
)
from jarvis.integrations.google.models import (
    ConnectionState,
    OAuthCallback,
)
from tests.integrations.google.fakes import (
    MATERIAL,
    FakeFlow,
    FakeHandle,
    FakeReceiver,
    FakeStore,
)


def make_connect_manager(
    *,
    callback_state: str | None = "expected",
    callback_error: str | None = None,
    receiver_error: str | None = None,
    handle: FakeHandle | None = None,
):
    store = FakeStore()
    current_handle = handle or FakeHandle()
    flow = FakeFlow("expected", current_handle)
    receiver = FakeReceiver(
        OAuthCallback(
            authorization_response="http://127.0.0.1/callback?code=authorization-secret",
            state=callback_state,
            error=callback_error,
        ),
        error=receiver_error,
    )
    manager = GoogleAuthManager(
        store,
        lambda _path, scopes, _state, redirect: (
            flow
            if scopes == ("openid",) and redirect == receiver.redirect_uri
            else (_ for _ in ()).throw(AssertionError("flow arguments"))
        ),
        lambda _material: current_handle,
        lambda: receiver,
        browser_open=lambda _url: True,
    )
    return manager, store, flow, receiver, current_handle


def test_connect_uses_explicit_consent_flow_and_persists_refresh_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.integrations.google.auth.secrets.token_urlsafe", lambda _n: "expected")
    manager, store, flow, receiver, _handle = make_connect_manager()

    status = manager.connect(Path("client.json"))

    assert status.state is ConnectionState.CONNECTED
    assert status.scopes == ("openid",)
    assert store.saved == [MATERIAL]
    assert len(flow.exchange_calls) == 1
    assert receiver.closed == 1


def test_connect_is_idempotent_when_credential_is_already_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.integrations.google.auth.secrets.token_urlsafe", lambda _n: "expected")
    manager, store, flow, _receiver, _handle = make_connect_manager()
    manager.connect(Path("client.json"))

    manager.connect(Path("client.json"))

    assert len(flow.exchange_calls) == 1
    assert len(store.saved) == 1


def test_consent_denied_never_exchanges_authorization_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.integrations.google.auth.secrets.token_urlsafe", lambda _n: "expected")
    manager, store, flow, _receiver, _handle = make_connect_manager(
        callback_error="access_denied"
    )

    with pytest.raises(GoogleAuthorizationDeniedError):
        manager.connect(Path("client.json"))

    assert flow.exchange_calls == []
    assert store.saved == []


def test_invalid_callback_state_fails_before_code_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.integrations.google.auth.secrets.token_urlsafe", lambda _n: "expected")
    manager, store, flow, _receiver, _handle = make_connect_manager(
        callback_state="attacker"
    )

    with pytest.raises(GoogleOAuthStateError):
        manager.connect(Path("client.json"))

    assert flow.exchange_calls == []
    assert store.saved == []


def test_invalid_state_from_flow_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.integrations.google.auth.secrets.token_urlsafe", lambda _n: "different")
    manager, _store, flow, _receiver, _handle = make_connect_manager()

    with pytest.raises(GoogleOAuthStateError):
        manager.connect(Path("client.json"))

    assert flow.exchange_calls == []


def test_timeout_and_cancellation_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.integrations.google.auth.secrets.token_urlsafe", lambda _n: "expected")
    timeout_manager, *_rest = make_connect_manager(receiver_error="timeout")
    with pytest.raises(GoogleAuthorizationTimeoutError):
        timeout_manager.connect(Path("client.json"), timeout_seconds=5)

    cancelled = threading.Event()
    cancelled.set()
    cancel_manager, *_rest = make_connect_manager()
    with pytest.raises(GoogleAuthorizationCancelledError):
        cancel_manager.connect(Path("client.json"), cancel=cancelled)


@pytest.mark.parametrize('deadline, expected', [(None, 600.0), (3.5, 3.5)])
def test_consent_has_time_for_login_without_ignoring_explicit_deadline(monkeypatch, deadline, expected):
    monkeypatch.setattr('jarvis.integrations.google.auth.secrets.token_urlsafe', lambda _n: 'expected')
    manager, _store, _flow, receiver, _handle = make_connect_manager()
    budgets = []
    original = receiver.wait
    def wait(budget, cancel, state):
        budgets.append(budget)
        return original(budget, cancel, state)
    monkeypatch.setattr(receiver, 'wait', wait)
    manager.connect(Path('client.json'), **({} if deadline is None else {'timeout_seconds': deadline}))
    assert budgets == [expected]
    assert receiver.closed == 1


def test_expired_login_closes_receiver_and_retry_uses_new_state(monkeypatch):
    states = iter(('expired-state', 'fresh-state'))
    monkeypatch.setattr('jarvis.integrations.google.auth.secrets.token_urlsafe', lambda _n: next(states))
    store, handle = FakeStore(), FakeHandle()
    first = FakeReceiver(error='timeout')
    second = FakeReceiver(OAuthCallback(
        authorization_response='http://127.0.0.1/callback?code=fresh-code', state='fresh-state', error=None))
    receivers = iter((first, second))
    flows = []
    def flow_factory(_path, _scopes, state, _redirect):
        flow = FakeFlow(state, handle)
        flows.append(flow)
        return flow
    manager = GoogleAuthManager(store, flow_factory, lambda _material: handle,
        lambda: next(receivers), browser_open=lambda _url: True)
    with pytest.raises(GoogleAuthorizationTimeoutError):
        manager.connect(Path('client.json'))
    assert first.closed == 1 and not store.saved and not flows[0].exchange_calls
    assert manager.current_status.state is ConnectionState.AUTHORIZATION_REQUIRED
    assert manager.current_status.error_code == 'authorization_timeout'
    manager.connect(Path('client.json'))
    assert [f.state for f in flows] == ['expired-state', 'fresh-state']
    assert second.closed == 1 and len(store.saved) == 1
    assert flows[1].exchange_calls == ['http://127.0.0.1/callback?code=fresh-code']


def test_missing_minimum_scope_is_rejected_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.integrations.google.auth.secrets.token_urlsafe", lambda _n: "expected")
    manager, store, _flow, _receiver, _handle = make_connect_manager(
        handle=FakeHandle(scopes=())
    )

    with pytest.raises(GoogleScopeError):
        manager.connect(Path("client.json"))

    assert len(store.saved) == 1
    assert store.saved[0].revocation_pending is True


def test_restore_refreshes_and_exposes_refreshing_state() -> None:
    started = threading.Event()
    release = threading.Event()
    handle = FakeHandle(
        valid=False,
        refresh_started=started,
        refresh_release=release,
    )
    store = FakeStore(MATERIAL)
    manager = GoogleAuthManager(
        store,
        lambda *_args: (_ for _ in ()).throw(AssertionError("oauth flow")),
        lambda _material: handle,
        lambda: (_ for _ in ()).throw(AssertionError("receiver")),
    )
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(manager.status()))
    thread.start()
    assert started.wait(1.0)
    assert manager.current_status.state is ConnectionState.TOKEN_REFRESHING
    release.set()
    thread.join(1.0)

    assert result[0].state is ConnectionState.CONNECTED
    assert handle.refresh_calls == 1
    assert len(store.saved) == 1


@pytest.mark.parametrize(
    ("refresh_error", "expected_state", "deleted"),
    [
        ("revoked", ConnectionState.REVOKED, 1),
        ("network", ConnectionState.ERROR, 0),
    ],
)
def test_refresh_failure_states(refresh_error, expected_state, deleted) -> None:
    handle = FakeHandle(valid=False, refresh_error=refresh_error)
    store = FakeStore(MATERIAL)
    manager = GoogleAuthManager(
        store,
        lambda *_args: None,
        lambda _material: handle,
        lambda: None,
    )

    status = manager.status()

    assert status.state is expected_state
    assert store.delete_calls == deleted


def test_disconnect_revokes_and_deletes_local_credential() -> None:
    handle = FakeHandle(valid=False)
    store = FakeStore(MATERIAL)
    manager = GoogleAuthManager(
        store,
        lambda *_args: None,
        lambda _material: handle,
        lambda: None,
    )

    first = manager.disconnect()
    second = manager.disconnect()

    assert first.state is ConnectionState.DISCONNECTED
    assert second.state is ConnectionState.DISCONNECTED
    assert handle.revoke_calls == 1
    assert store.delete_calls == 2


def test_disconnect_removes_local_credential_when_remote_revoke_fails() -> None:
    handle = FakeHandle(valid=False, revoke_error=True)
    store = FakeStore(MATERIAL)
    manager = GoogleAuthManager(
        store,
        lambda *_args: None,
        lambda _material: handle,
        lambda: None,
    )

    status = manager.disconnect()

    assert status.state is ConnectionState.DISCONNECTED
    assert status.error_code == "remote_revocation_unconfirmed"
    assert store.material is not None
    assert store.material.revocation_pending is True

    restored = GoogleAuthManager(
        store,
        lambda *_args: None,
        lambda _material: handle,
        lambda: None,
    )
    assert restored.status().error_code == "revocation_pending"


def test_scope_failure_cannot_become_connected_on_later_status() -> None:
    handle = FakeHandle(valid=False, scopes=())
    store = FakeStore(MATERIAL)
    manager = GoogleAuthManager(
        store,
        lambda *_args: None,
        lambda _material: handle,
        lambda: None,
    )

    first = manager.status()
    second = manager.status()

    assert first.state is ConnectionState.ERROR
    assert second.state is not ConnectionState.CONNECTED
    assert store.material is not None
    assert store.material.revocation_pending is True


def test_connect_cannot_overwrite_pending_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from jarvis.integrations.google.errors import GoogleRevocationError

    store = FakeStore(replace(MATERIAL, revocation_pending=True))
    flow_calls: list[bool] = []
    manager = GoogleAuthManager(
        store,
        lambda *_args: flow_calls.append(True),
        lambda _material: FakeHandle(),
        lambda: None,
    )

    with pytest.raises(GoogleRevocationError, match="pendente"):
        manager.connect(Path("client.json"))

    assert flow_calls == []
    assert store.material.revocation_pending is True


def test_calendar_scope_upgrade_preserves_basic_credential() -> None:
    handle = FakeHandle(valid=False, scopes=("openid",))
    store = FakeStore(MATERIAL)
    manager = GoogleAuthManager(
        store,
        lambda *_args: None,
        lambda _material: handle,
        lambda: None,
        scopes=("openid", "calendar.readonly"),
        upgrade_from_scopes=("openid",),
    )

    status = manager.status()

    assert status.state is ConnectionState.AUTHORIZATION_REQUIRED
    assert status.error_code == "scope_upgrade_required"
    assert store.material == MATERIAL
    assert store.material.revocation_pending is False


def test_calendar_manager_rejects_credentials_with_additional_scopes() -> None:
    from jarvis.integrations.google.scopes import CALENDAR_SCOPES

    granted = (*CALENDAR_SCOPES, "https://www.googleapis.com/auth/calendar")
    handle = FakeHandle(valid=False, scopes=granted)
    store = FakeStore(MATERIAL)
    manager = GoogleAuthManager(
        store,
        lambda *_args: None,
        lambda _material: handle,
        lambda: None,
        scopes=CALENDAR_SCOPES,
        upgrade_from_scopes=("openid",),
        allowed_scopes=CALENDAR_SCOPES,
    )

    status = manager.status()

    assert status.state is ConnectionState.ERROR
    assert status.error_code == "credential_scope_error"
    assert store.material is not None
    assert store.material.revocation_pending is True


def test_quarantine_failure_deletes_active_local_material() -> None:
    from jarvis.integrations.google.errors import GoogleCredentialStorageError

    class FailingStore(FakeStore):
        def save(self, _material) -> None:
            raise GoogleCredentialStorageError("disk")

    handle = FakeHandle(valid=False, revoke_error=True)
    store = FailingStore(MATERIAL)
    manager = GoogleAuthManager(
        store,
        lambda *_args: None,
        lambda _material: handle,
        lambda: None,
    )

    status = manager.disconnect()

    assert status.error_code == "remote_revocation_unconfirmed_local_removed"
    assert store.material is None
    assert handle.revoke_calls == 0


def test_sensitive_models_never_render_secrets() -> None:
    rendered = repr(MATERIAL)
    callback = OAuthCallback(
        authorization_response="?code=authorization-secret",
        state="state-secret",
    )

    assert "refresh-secret" not in rendered
    assert "client-secret" not in rendered
    assert "authorization-secret" not in repr(callback)
    assert "state-secret" not in repr(callback)


def test_storage_corruption_is_aggregated_without_secret() -> None:
    class CorruptStore(FakeStore):
        def load(self):
            raise GoogleCredentialStorageCorruptError("safe storage error")

    manager = GoogleAuthManager(
        CorruptStore(),
        lambda *_args: None,
        lambda _material: None,
        lambda: None,
    )

    status = manager.status()

    assert status.state is ConnectionState.ERROR
    assert status.error_code == "storage_corrupt"


def test_oauth_secrets_never_enter_logs_or_audit(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("jarvis.integrations.google.auth.secrets.token_urlsafe", lambda _n: "expected")
    manager, _store, _flow, _receiver, _handle = make_connect_manager(
        callback_error="access_denied"
    )
    with pytest.raises(GoogleAuthorizationDeniedError):
        manager.connect(Path("client-secret-file.json"))

    combined = caplog.text + "".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.glob("**/*")
        if path.is_file()
    )
    assert "authorization-secret" not in combined
    assert "refresh-secret" not in combined
    assert "client-secret" not in combined
