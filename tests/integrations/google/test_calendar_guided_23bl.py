"""Scope-correct read-only discovery and unchanged operational authority."""
from dataclasses import replace

import pytest

from jarvis.integrations.google.storage import ProtectedFileCredentialStore
from tests.integrations.google.test_accounts_23bk import make, verify


class Api:
    def __init__(self, *, shared=False, changed=False):
        self.calls = []
        self.shared, self.changed = shared, changed

    def get(self, path, params):
        self.calls.append(path)
        if path.endswith('/acl'):
            rows = [{'role': 'owner', 'scope': {'type': 'user'}}]
            if self.shared:
                rows.append({'role': 'reader', 'scope': {'type': 'user'}})
            return {'etag': 'private-version', 'items': rows}
        if path == '/users/me/calendarList':
            return {'items': [{'id': 'synthetic-calendar', 'summary': 'Agenda real', 'accessRole': 'owner',
                               'timeZone': 'America/Sao_Paulo'}]}
        assert path == '/calendars/synthetic-calendar'
        return {'id': 'other-resource' if self.changed else 'synthetic-calendar',
                'summary': 'Agenda real', 'timeZone': 'America/Sao_Paulo'}


def test_minimal_existing_grant_auto_loads_metadata_not_calendar_list_and_no_oauth(tmp_path):
    service, handles, _ = make(tmp_path)
    api = Api()
    service.calendar_client_factory = lambda auth: api
    before = {p.name: p.read_bytes() for p in service.directory.glob('credentials*')}
    materials = {p.name: ProtectedFileCredentialStore(p, service.protector).load()
                 for p in service.directory.glob('credentials*')}
    data = service.guided_calendar()
    assert data['matching_writer'] and len(data['calendars']) == 1
    assert data['calendars'][0]['write_candidate']
    assert api.calls == ['/calendars/synthetic-calendar', '/calendars/synthetic-calendar/acl']
    assert materials == {p.name: ProtectedFileCredentialStore(p, service.protector).load()
                         for p in service.directory.glob('credentials*')}
    assert all((service.directory/name).read_bytes() == raw for name, raw in before.items()
               if name not in {'credentials_calendar_read.bin', 'credentials_calendar_limited_write.bin'})
    assert not data['write_enabled'] and all(h.revoke_calls == 0 for h in handles.values())
    reader = service.default('calendar_read')[0]
    writer = service.default('calendar_write')[0]
    service.select_calendar(reader, writer, data['revision'], 'synthetic-calendar', 'teste')
    assert not service.config.google_calendar_write_enabled


def test_already_granted_list_scope_uses_list_without_broadening(tmp_path):
    service, *_ = make(tmp_path)
    store = ProtectedFileCredentialStore(service.directory/'credentials_calendar_read.bin', service.protector)
    scopes = (*store.load().scopes, 'https://www.googleapis.com/auth/calendar.calendarlist.readonly')
    store.save(replace(store.load(), scopes=scopes))
    api = Api()
    service.calendar_client_factory = lambda auth: api
    data = service.guided_calendar()
    assert api.calls[0] == '/users/me/calendarList' and data['calendars'][0]['label'] == 'Agenda real'
    assert store.load().scopes == scopes


def test_reconnected_minimal_read_explicit_selection_without_list_or_write(tmp_path):
    service, handles, _ = make(tmp_path)
    reader = verify(service, 'calendar_read')
    state = service._read()
    state['connections'][reader]['resources'] = {}
    service._save(state)
    api = Api()
    service.calendar_client_factory = lambda auth: api
    writer_before = dict(service.default('calendar_write')[1])
    material_before = service._material(service.default('calendar_read')[1])
    service.select_calendar(reader, None, service.inventory()['revision'],
                            'synthetic-calendar', 'teste', calendar_allowed=lambda value: True)
    assert api.calls == ['/calendars/synthetic-calendar']
    assert service.default('calendar_read')[1]['resources'] == {'teste': 'synthetic-calendar'}
    assert service.default('calendar_write')[1] == writer_before
    assert service._material(service.default('calendar_read')[1]) == material_before
    assert not service.config.google_calendar_write_enabled
    assert all(handle.revoke_calls == 0 for handle in handles.values())


@pytest.mark.parametrize('failure', ['blocked', 'no_policy', 'wrong_id', 'unverified', 'concurrent'])
def test_reconnected_read_selection_fails_closed(tmp_path, failure):
    service, *_ = make(tmp_path)
    reader = verify(service, 'calendar_read')
    state = service._read()
    state['connections'][reader]['resources'] = {}
    service._save(state)
    api = Api(changed=failure == 'wrong_id')

    class GuardedApi:
        def __init__(self, auth):
            self.auth = auth

        def get(self, path, params):
            with self.auth.request_guard():
                result = api.get(path, params)
            if failure == 'concurrent':
                service.change(reader, service.inventory()['revision'], 'label', 'Changed')
            return result

    service.calendar_client_factory = GuardedApi
    if failure == 'unverified':
        service._verified.clear()
    policy = None if failure == 'no_policy' else lambda value: failure != 'blocked'
    with pytest.raises(PermissionError):
        service.select_calendar(reader, None, service.inventory()['revision'],
                                'synthetic-calendar', 'teste', calendar_allowed=policy)
    assert service.default('calendar_read')[1]['resources'] == {}
    assert not service.config.google_calendar_write_enabled
    if failure in {'blocked', 'no_policy', 'unverified'}:
        assert api.calls == []


@pytest.mark.parametrize('concurrent_change', [False, True])
def test_selection_uses_real_request_guard_and_revalidates_after_acl(tmp_path, concurrent_change):
    service, *_ = make(tmp_path)
    api = Api()
    armed = False

    class GuardedApi:
        def __init__(self, auth):
            self.auth = auth

        def get(self, path, params):
            with self.auth.request_guard():
                result = api.get(path, params)
            if armed and concurrent_change and path.endswith('/acl'):
                key = service.default('calendar_write')[0]
                service.change(key, service.inventory()['revision'], 'label', 'Changed')
            return result

    service.calendar_client_factory = GuardedApi
    data = service.guided_calendar()
    reader = service.default('calendar_read')[0]
    writer = service.default('calendar_write')[0]
    before = dict(service.default('calendar_write')[1]['resources'])
    armed = True
    if concurrent_change:
        with pytest.raises(PermissionError):
            service.select_calendar(reader, writer, data['revision'], 'synthetic-calendar', 'canary')
        assert service.default('calendar_write')[1]['resources'] == before
    else:
        service.select_calendar(reader, writer, data['revision'], 'synthetic-calendar', 'canary')
        assert service.default('calendar_write')[1]['resources'] == {'canary': 'synthetic-calendar'}
    assert not service.config.google_calendar_write_enabled
    # Every guard must release the real cross-process lock, including denial.
    with service.lock_factory():
        pass


@pytest.mark.parametrize('problem', ['shared', 'identity', 'metadata', 'scope'])
def test_guided_unavailable_write_never_grants_or_reconnects(tmp_path, problem):
    service, *_ = make(tmp_path)
    api = Api(shared=problem == 'shared', changed=problem == 'metadata')
    service.calendar_client_factory = lambda auth: api
    if problem == 'identity':
        service.identity = lambda auth, op: ('google.sub', 'other' if op == 'calendar_write' else 'reader')
    if problem == 'scope':
        store = ProtectedFileCredentialStore(service.directory/'credentials_calendar_read.bin', service.protector)
        store.save(replace(store.load(), scopes=('openid',)))
    before = service.inventory()
    data = service.guided_calendar()
    if problem == 'scope':
        assert data['calendar_state'] == 'consent_required'
        assert 'falta consentimento' in data['calendar_notice'] and 'Conectar/reconectar' in data['calendar_notice']
    assert not data['write_enabled']
    assert not any(r['write_candidate'] for r in data['calendars'])
    assert next(r for r in data['rows'] if r['operation'] == 'calendar_write')['resources'] == next(
        r for r in before['rows'] if r['operation'] == 'calendar_write')['resources']
    if problem == 'shared':
        with pytest.raises(PermissionError):
            service.select_calendar(service.default('calendar_read')[0], service.default('calendar_write')[0],
                data['revision'], 'synthetic-calendar', 'teste')


def test_discovery_filters_scoped_block_before_metadata_and_has_no_fallback(tmp_path):
    service, *_ = make(tmp_path)
    api = Api()
    service.calendar_client_factory = lambda auth: api
    data = service.guided_calendar(calendar_allowed=lambda _: False)
    assert data['calendars'] == [] and api.calls == []
    reader = verify(service, 'calendar_read')
    service.change(reader, service.inventory()['revision'], 'disconnect', True)
    data = service.guided_calendar()
    assert data['calendars'] == [] and api.calls == []


@pytest.mark.parametrize('error,expected', [
    ('GooglePermissionError', 'access_denied'),
    ('GoogleResourceNotFoundError', 'resource_missing'),
    ('GoogleRateLimitError', 'unavailable'),
    ('GoogleNetworkError', 'unavailable'),
])
def test_google_discovery_failure_explains_action_without_credential_payload(tmp_path, error, expected):
    from jarvis.integrations.google import errors
    service, *_ = make(tmp_path)
    class UnavailableApi:
        def get(self, path, params):
            raise getattr(errors, error)('private-token-private-calendar')
    service.calendar_client_factory = lambda auth: UnavailableApi()
    data = service.guided_calendar()
    assert data['calendar_state'] == expected and data['calendars'] == []
    assert 'private-token' not in data['calendar_notice']
    assert not service.config.google_calendar_write_enabled
