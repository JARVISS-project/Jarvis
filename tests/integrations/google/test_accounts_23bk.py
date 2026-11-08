"""Real account store/DPAPI/auth lifecycle; external Google boundaries are fake."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.config import JarvisConfig
from jarvis.integrations.google.accounts import AccountService, PROFILES, grant_digest
from jarvis.integrations.google.auth import GoogleAuthManager
from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
from jarvis.integrations.google.scopes import CALENDAR_MINIMAL_READ_SCOPES, CALENDAR_LIMITED_WRITE_SCOPES, DRIVE_PICKER_SCOPES
from jarvis.integrations.google.storage import ProtectedFileCredentialStore, WindowsDPAPIProtector
from tests.integrations.google.fakes import MATERIAL, FakeHandle


def make(tmp_path):
    config = JarvisConfig(settings_path=tmp_path/'settings.json',
        google_calendar_authorized_calendars={'agenda': 'synthetic-calendar'},
        google_calendar_write_calendars={'teste': 'synthetic-calendar'},
        google_calendar_enabled=True)
    directory = tmp_path/'protected-google'
    protector = WindowsDPAPIProtector()
    handles, managers = {}, {}
    scopes = {'calendar_read': CALENDAR_MINIMAL_READ_SCOPES, 'calendar_write': CALENDAR_LIMITED_WRITE_SCOPES,
              'drive_docs_read': DRIVE_PICKER_SCOPES}
    for operation, (filename, _) in PROFILES.items():
        material = replace(MATERIAL, refresh_token='synthetic-' + operation, scopes=scopes.get(operation, ('openid',)))
        ProtectedFileCredentialStore(directory/filename, protector).save(material)

    def auth_factory(operation, path):
        if path not in managers:
            store = ProtectedFileCredentialStore(path, protector)
            material = store.load() or replace(MATERIAL, refresh_token='new-' + path.stem, scopes=scopes[operation])
            handle = FakeHandle(scopes=material.scopes)
            handle.storage_material = lambda: material
            handles[path] = handle
            managers[path] = GoogleAuthManager(store, lambda *args: pytest.fail('OAuth not expected'),
                lambda stored: handle, lambda: pytest.fail('Browser not expected'),
                scopes=material.scopes, allowed_scopes=material.scopes)
        return managers[path]
    service = AccountService(config, directory=directory, protector=protector,
        auth_factory=auth_factory, identity=lambda auth, operation: ('google.sub', 'synthetic-user'))
    return service, handles, managers


def row(service, operation):
    return next(r for r in service.inventory()['rows'] if r['operation'] == operation and r['default'])


def verify(service, operation):
    data = service.inventory()
    key = next(r['id'] for r in data['rows'] if r['operation'] == operation and r['default'])
    service.test(key, data['revision'])
    return key


def test_migration_preserves_bytes_scopes_resources_and_restarts(tmp_path):
    service, *_ = make(tmp_path)
    before = {p.name: p.read_bytes() for p in service.directory.glob('credentials*')}
    data = service.inventory()
    assert len(data['rows']) == 5 and not data['write_enabled']
    assert all(r['status'] != 'conectado' for r in data['rows'])
    assert before == {p.name: p.read_bytes() for p in service.directory.glob('credentials*')}
    assert row(service, 'calendar_read')['resources'] == {'agenda': 'synthetic-calendar'}
    restarted = AccountService(service.config, directory=service.directory, protector=service.protector)
    assert restarted.inventory()['rows'] == data['rows']
    assert b'synthetic-user' not in service.path.read_bytes()
    assert not list(service.directory.glob('.accounts-*'))


def test_new_account_default_never_transfers_grants_and_old_runtime_blocks(tmp_path):
    service, *_ = make(tmp_path)
    verify(service, 'calendar_read')
    old = service.auth('calendar_read')
    old.credentials()
    data = service.inventory()
    new = service.add('calendar_read', 'Trabalho escolhido', data['revision'])
    key = new['connection']
    assert next(r for r in new['rows'] if r['id'] == key)['resources'] == {}
    service.change(key, new['revision'], 'default', True)
    with pytest.raises(PermissionError):
        old.credentials()
    assert service.default('calendar_read')[0] == key
    with pytest.raises(PermissionError):
        service.auth('calendar_read').credentials()  # No fallback to legacy account.
    restarted = AccountService(service.config, directory=service.directory, protector=service.protector)
    assert restarted.default('calendar_read')[0] == key


def test_identity_mismatch_blocks_prepared_and_clears_resources(tmp_path):
    service, *_ = make(tmp_path)
    key = verify(service, 'calendar_write')
    old = service.auth('calendar_write')
    old.binding()
    service.identity = lambda *args: ('google.sub', 'different-user')
    with pytest.raises(PermissionError):
        service.test(key, service.inventory()['revision'])
    with pytest.raises(PermissionError):
        old.credentials()
    assert row(service, 'calendar_write')['resources'] == {}


def test_local_disconnect_blocks_cached_token_without_remote_revoke(tmp_path):
    service, handles, _ = make(tmp_path)
    key = verify(service, 'calendar_read')
    old = service.auth('calendar_read')
    old.credentials()
    service.change(key, service.inventory()['revision'], 'disconnect', True)
    with pytest.raises(PermissionError):
        old.credentials()
    assert all(h.revoke_calls == 0 for h in handles.values())
    assert row(service, 'calendar_read')['status'] == 'não conectado'
    assert row(service, 'drive_docs_read')['scopes'] == list(DRIVE_PICKER_SCOPES)


@pytest.mark.parametrize('corruption', ['invalid-json', 'version', 'credential', 'default', 'resource', 'extra'])
def test_invalid_registry_fails_closed_without_replacing_it(tmp_path, corruption):
    service, *_ = make(tmp_path)
    service.inventory()
    state = service._read()
    key = state['defaults']['calendar_read']
    if corruption == 'invalid-json':
        raw = b'{bad'
    else:
        if corruption == 'version': state['version'] = 2
        elif corruption == 'credential': state['connections'][key]['credential'] = '../credentials.bin'
        elif corruption == 'default': state['defaults']['calendar_read'] = state['defaults']['calendar_write']
        elif corruption == 'resource': state['connections'][key]['resources']['bad'] = '../\x00private'
        else: state['extra'] = True
        raw = json.dumps(state).encode()
    service.path.write_bytes(service.protector.protect(raw))
    before = service.path.read_bytes()
    with pytest.raises(PermissionError): service.inventory()
    assert service.path.read_bytes() == before


def test_stale_revision_and_atomic_save_failure_leave_previous_state(tmp_path, monkeypatch):
    service, *_ = make(tmp_path)
    data = service.inventory()
    key = row(service, 'calendar_read')['id']
    service.change(key, data['revision'], 'label', 'Pessoal por escolha')
    with pytest.raises(PermissionError): service.change(key, data['revision'], 'default', True)
    before = service.path.read_bytes()
    monkeypatch.setattr('jarvis.integrations.google.accounts.os.replace', lambda *args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError): service.add('calendar_read', 'Outra', service.inventory()['revision'])
    assert service.path.read_bytes() == before and not list(service.directory.glob('.accounts-*'))


def test_credential_replaced_or_expired_never_claims_connected(tmp_path):
    service, _, managers = make(tmp_path)
    key = verify(service, 'calendar_read')
    old = service.auth('calendar_read')
    path = service._credential(old.snapshot)
    store = ProtectedFileCredentialStore(path, service.protector)
    store.save(replace(store.load(), refresh_token='different-secret'))
    with pytest.raises(PermissionError): old.credentials()
    assert row(service, 'calendar_read')['status'] == 'erro'
    manager = managers[path]
    manager._handle = None
    handle = FakeHandle(valid=False, refresh_error='revoked')
    manager._credential_loader = lambda material: handle
    service.test(key, service.inventory()['revision'])
    assert row(service, 'calendar_read')['status'] == 'expirado'
    assert manager.current_status.state is ConnectionState.REVOKED


def test_write_requires_explicit_identity_and_flag_stays_false(tmp_path):
    service, *_ = make(tmp_path)
    auth = service.auth('calendar_write')
    with pytest.raises(PermissionError): auth.binding()
    verify(service, 'calendar_write')
    service.auth('calendar_write').credentials()
    assert service.config.google_calendar_write_enabled is False


def test_request_guard_prevents_disconnect_during_effect(tmp_path):
    from threading import Thread
    service, *_ = make(tmp_path)
    key = verify(service, 'calendar_read')
    other = AccountService(service.config, directory=service.directory, protector=service.protector,
                           auth_factory=service.auth_factory, identity=service.identity)
    auth = service.auth('calendar_read')
    failures = []
    def revoke():
        try: other.change(key, service._read()['revision'], 'disconnect', True)
        except Exception as error: failures.append(type(error).__name__)
    with auth.request_guard():
        thread = Thread(target=revoke)
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and failures == ['GoogleOperationInProgressError']
        auth.credentials()
    other.change(key, service.inventory()['revision'], 'disconnect', True)
    with pytest.raises(PermissionError): auth.credentials()


def test_calendar_selection_same_identity_owner_and_no_write_activation(tmp_path, monkeypatch):
    service, *_ = make(tmp_path)
    store = ProtectedFileCredentialStore(service.directory/'credentials_calendar_read.bin', service.protector)
    store.save(replace(store.load(), scopes=('openid', 'https://www.googleapis.com/auth/calendar.readonly')))
    reader = verify(service, 'calendar_read')
    writer = verify(service, 'calendar_write')
    def get(_client, path, *args, **kwargs):
        if path.endswith('/acl'):
            return {'etag': 'private-version', 'items': [{'role': 'owner', 'scope': {'type': 'user'}}]}
        assert path == '/users/me/calendarList'
        return {'items': [{'id': 'chosen', 'summary': '<img src=x>', 'accessRole': 'owner'}]}
    monkeypatch.setattr('jarvis.integrations.google.calendar.client.GoogleCalendarApiClient.get', get)
    service.select_calendar(reader, writer, service.inventory()['revision'], 'chosen', 'test')
    assert row(service, 'calendar_write')['resources'] == {'test': 'chosen'}
    assert row(service, 'calendar_read')['resources'] == {'agenda': 'synthetic-calendar'}
    assert not service.config.google_calendar_write_enabled
    with pytest.raises(PermissionError):
        service.select_calendar(reader, writer, service.inventory()['revision'], 'not-listed', 'test')
    monkeypatch.setattr('jarvis.integrations.google.calendar.client.GoogleCalendarApiClient.get',
        lambda *args, **kwargs: {'items': [{'id': 'chosen', 'accessRole': 'writer'}]})
    with pytest.raises(PermissionError):
        service.select_calendar(reader, writer, service.inventory()['revision'], 'chosen', 'test')


def test_account_change_after_calendar_prepare_authorizes_zero_effects(tmp_path):
    from tests.integrations.google.test_limited_calendar_23bg import setup, create_command
    service, *_ = make(tmp_path)
    key = verify(service, 'calendar_write')
    auth = service.auth('calendar_write')
    h, api, _, create, _, audit = setup(tmp_path/'flow')
    create._grant_binding = auth.binding
    create._account_label = lambda: 'Synthetic account'
    try:
        response = h.processar_mensagem(create_command())
        assert 'Synthetic account' in response
        pending = h.confirmation_sessions.current()
        service.change(key, service.inventory()['revision'], 'disconnect', True)
        h.processar_mensagem('sim', confirmation_session_id=pending.id, confirmation_challenge_id=pending.challenge_id)
        assert api.posts == api.patches == 0
        assert 'refresh-secret' not in audit.read_text()
    finally:
        h.close()


def test_invalid_settings_never_enables_calendar_flags(tmp_path):
    path = tmp_path/'settings.json'
    path.write_text('{"google_calendar_write":"true"}')
    config = JarvisConfig(settings_path=path)
    assert not config.google_calendar_write_enabled
    for method in (config.save_calendar_settings, config.save_calendar_write_settings):
        with pytest.raises(ValueError): method(enabled=True)
    assert path.read_text() == '{"google_calendar_write":"true"}'


def test_connect_new_account_real_oauth_lifecycle_separate_scopes_and_no_grants(tmp_path, monkeypatch):
    from tests.integrations.google.fakes import FakeFlow, FakeReceiver
    from jarvis.integrations.google.models import OAuthCallback
    service, handles, managers = make(tmp_path)
    initial = service.inventory()
    added = service.add('calendar_write', 'Test account', initial['revision'])
    key = added['connection']
    state = service._read()
    path = service._credential(state['connections'][key])
    auth = service.auth_factory('calendar_write', path)
    handle = handles[path]
    flow = FakeFlow('expected', handle)
    receiver = FakeReceiver(OAuthCallback(authorization_response='http://127.0.0.1/callback?code=synthetic', state='expected'))
    auth._oauth_flow_factory = lambda *args: flow
    auth._callback_receiver_factory = lambda: receiver
    auth._browser_open = lambda url: True
    monkeypatch.setattr('jarvis.integrations.google.auth.secrets.token_urlsafe', lambda length: 'expected')
    result = service.connect(key, added['revision'], str(tmp_path/'client.json'))
    connected = next(r for r in result['rows'] if r['id'] == key)
    assert connected['status'] == 'conectado' and connected['resources'] == {}
    assert connected['scopes'] == list(CALENDAR_LIMITED_WRITE_SCOPES)
    assert not connected['default'] and not result['write_enabled']
    assert len(flow.exchange_calls) == 1 and receiver.closed == 1
    assert service.default('calendar_write')[0] != key
    assert b'new-account' not in service.path.read_bytes()


@pytest.mark.parametrize('operation,namespace,value', [
    ('calendar_read', 'google.sub', {'sub': 'synthetic-sub'}),
    ('drive_docs_read', 'drive.permissionId', {'user': {'permissionId': 'synthetic-id'}})])
def test_identity_get_only_bounded_and_private(operation, namespace, value, monkeypatch):
    from io import BytesIO
    from jarvis.integrations.google.accounts import google_identity
    calls = []
    def opener(request, timeout):
        calls.append(request)
        assert request.get_method() == 'GET' and timeout == 10.0
        assert request.get_header('Authorization') == 'Bearer synthetic-token'
        return BytesIO(json.dumps(value).encode())
    monkeypatch.setattr('jarvis.integrations.google.accounts.urlopen', opener)
    result = google_identity(SimpleNamespace(credentials=lambda: SimpleNamespace(access_token='synthetic-token')), operation)
    assert result[0] == namespace and len(calls) == 1
    assert 'synthetic-token' not in calls[0].full_url


@pytest.mark.parametrize('payload', [b'{"sub":"a","sub":"b"}', b'{"sub":null}', b'{"sub":"bad email"}', b'x'*65537],
                         ids=['duplicate', 'missing', 'invalid', 'oversized'])
def test_identity_invalid_never_returns_account(payload, monkeypatch):
    from io import BytesIO
    from jarvis.integrations.google.accounts import google_identity
    monkeypatch.setattr('jarvis.integrations.google.accounts.urlopen', lambda *args, **kwargs: BytesIO(payload))
    with pytest.raises(RuntimeError):
        google_identity(SimpleNamespace(credentials=lambda: SimpleNamespace(access_token='private')), 'calendar_read')


def test_calendar_write_wrong_account_selection_rejected(tmp_path, monkeypatch):
    service, *_ = make(tmp_path)
    store = ProtectedFileCredentialStore(service.directory/'credentials_calendar_read.bin', service.protector)
    store.save(replace(store.load(), scopes=('openid', 'https://www.googleapis.com/auth/calendar.readonly')))
    reader = verify(service, 'calendar_read')
    service.identity = lambda *args: ('google.sub', 'other-user')
    writer = verify(service, 'calendar_write')
    monkeypatch.setattr('jarvis.integrations.google.calendar.client.GoogleCalendarApiClient.get',
        lambda *args, **kwargs: {'items': [{'id': 'chosen', 'accessRole': 'owner'}]})
    before = row(service, 'calendar_write')['resources']
    with pytest.raises(PermissionError):
        service.select_calendar(reader, writer, service.inventory()['revision'], 'chosen', 'test')
    assert row(service, 'calendar_write')['resources'] == before


@pytest.mark.parametrize('error_kind', ['permission', '403'])
def test_write_verification_revoked_after_effect_is_pending_not_success(tmp_path, error_kind):
    from tests.integrations.google.test_limited_calendar_23bg import setup, create_command
    from jarvis.integrations.google.errors import GooglePermissionError
    h, api, *_ = setup(tmp_path)
    original_get = api.get
    def get(path, params):
        if api.posts and not path.endswith(('/acl', '/events')):
            raise PermissionError('revoked') if error_kind == 'permission' else GooglePermissionError('403')
        return original_get(path, params)
    api.get = get
    try:
        h.processar_mensagem(create_command())
        pending = h.confirmation_sessions.current()
        result = h.processar_mensagem('sim', confirmation_session_id=pending.id, confirmation_challenge_id=pending.challenge_id)
        assert 'pendente de reconciliação' in result and api.posts == 1
        assert 'criado e verificado' not in result
    finally: h.close()


def test_update_preview_shows_old_new_and_account_bound_values(tmp_path):
    from tests.integrations.google.test_limited_calendar_23bg import setup, update_command
    h, api, _, _, update, _ = setup(tmp_path)
    update._account_label = lambda: 'Synthetic account'
    api.seed()
    try:
        text = h.processar_mensagem(update_command())
        assert 'Synthetic account' in text and 'Anterior: Original' in text and 'Título privado' in text
        assert api.posts == api.patches == 0
    finally: h.close()


def test_legacy_cli_blocks_other_default_without_calling_provider(tmp_path, monkeypatch, capsys):
    from jarvis.runtime.cli import _handle_google_command
    service, *_ = make(tmp_path)
    data = service.inventory()
    added = service.add('calendar_read', 'Other account', data['revision'])
    service.change(added['connection'], added['revision'], 'default', True)
    monkeypatch.setenv('JARVIS_GOOGLE_CREDENTIAL_DIR', str(service.directory))
    monkeypatch.setattr('jarvis.config.JarvisConfig', lambda: service.config)
    monkeypatch.setattr('jarvis.integrations.google.factory.build_google_calendar_minimal_read_auth',
                        lambda: pytest.fail('No silent fallback to legacy credentials'))
    args = SimpleNamespace(google_timeout=120, google_calendar='status', google_calendar_write=None,
                           google_drive_docs=None, google=None)
    assert _handle_google_command(args) == 1
    assert 'Nenhum fallback' in capsys.readouterr().out


def test_legacy_remote_disconnect_requires_scope_preview_when_registry_active(tmp_path, monkeypatch, capsys):
    from jarvis.runtime.cli import _handle_google_command
    service, handles, _ = make(tmp_path)
    service.inventory()
    monkeypatch.setenv('JARVIS_GOOGLE_CREDENTIAL_DIR', str(service.directory))
    monkeypatch.setattr('jarvis.config.JarvisConfig', lambda: service.config)
    args = SimpleNamespace(google_timeout=120, google_calendar='disconnect', google_calendar_write=None,
                           google_drive_docs=None, google=None)
    assert _handle_google_command(args) == 1
    assert 'nenhuma revogação executada' in capsys.readouterr().out
    assert all(handle.revoke_calls == 0 for handle in handles.values())


def test_bound_transport_rejects_changed_account_before_http(tmp_path, monkeypatch):
    from jarvis.integrations.google.calendar.client import GoogleCalendarApiClient
    service, *_ = make(tmp_path)
    key = verify(service, 'calendar_read')
    client = GoogleCalendarApiClient(service.auth('calendar_read'))
    service.change(key, service.inventory()['revision'], 'label', 'New label')
    monkeypatch.setattr('jarvis.integrations.google.calendar.client.urlopen',
                        lambda *args, **kwargs: pytest.fail('No API request after invalidation'))
    with pytest.raises(PermissionError): client.get('/users/me/calendarList', {})


def test_close_signal_before_oauth_cannot_be_cleared_or_launch_browser(tmp_path):
    service, *_ = make(tmp_path)
    data = service.inventory()
    added = service.add('calendar_read', 'Canceled connection', data['revision'])
    key = added['connection']
    material_store = ProtectedFileCredentialStore(service._credential(service._read()['connections'][key]), service.protector)
    calls = []
    def status():
        service.cancel_connection()  # Controller closes while pre-OAuth status finishes.
        return ConnectionStatus(ConnectionState.DISCONNECTED)
    def connect(path, *, cancel):
        calls.append(path)
        assert cancel.is_set(), 'Shutdown cancellation must not be cleared before OAuth'
        raise PermissionError('Canceled before browser')
    service.auth_factory = lambda *args: SimpleNamespace(status=status, connect=connect)
    with pytest.raises(PermissionError): service.connect(key, added['revision'], 'synthetic-client.json')
    assert material_store.load() is None
    assert calls == []
