import pytest

from jarvis.security.capabilities import Capability
from jarvis.security.permissions import scoped_resource
from tests.integration.test_administration_23bh import change
from tests.ui.test_accounts_widgets_23bk import system
from tests.ui.test_administrative_widgets_23bh import close_system, settle


def setup_system(tmp_path):
    result = system(tmp_path)
    app, h, service, controller, window, *_ = result
    accounts = service.accounts
    accounts.initialize()
    service.config.google_calendar_enabled = False
    accounts.config = service.config
    state = accounts._read()
    reader = state['defaults']['calendar_read']
    state['connections'][reader]['resources'] = {}
    accounts._save(state)
    accounts.test(reader, accounts.inventory()['revision'])
    calls = []

    class MetadataOnly:
        def get(self, path, params):
            calls.append(path)
            assert path in {'/calendars/primary', '/calendars/synthetic-calendar'}
            return {'id': 'synthetic-calendar', 'summary': 'Minha agenda',
                    'timeZone': 'America/Sao_Paulo'}

    accounts.calendar_client_factory = lambda auth: MetadataOnly()
    return result, reader, calls


def test_setup_from_disabled_read_can_select_before_enabling_without_writes(tmp_path):
    result, reader, calls = setup_system(tmp_path)
    app, h, service, controller, window, *_ = result
    try:
        page = window.accounts_page
        window._show_page('Contas e conexões')
        settle(app, lambda: not page.busy)
        assert calls == []  # Opening disabled setup does not contact Google.
        assert not page.read_enabled.isEnabled()
        assert page.event_options.isHidden()
        writer_before = dict(service.accounts.default('calendar_write')[1])
        page.primary_button.click()
        settle(app, lambda: not page.busy)
        assert calls == ['/calendars/primary']
        assert '1 agenda(s)' in page.calendar_state.text()
        assert page.calendars.currentIndex() == -1
        assert service.accounts.default('calendar_read')[1]['resources'] == {}
        page.calendars.setCurrentIndex(0)
        page.select_button.click()
        settle(app, lambda: not page.busy)
        assert service.accounts.default('calendar_read')[1]['resources'] == {'principal': 'synthetic-calendar'}
        assert not service.config.google_calendar_enabled
        assert service.accounts.default('calendar_write')[1] == writer_before
        assert page.read_enabled.isEnabled()
        page.read_enabled.click()
        settle(app, lambda: not page.busy)
        assert service.config.google_calendar_enabled
        assert not service.config.google_calendar_write_enabled
        assert not service.capability_policy.is_enabled(Capability.GOOGLE_CALENDAR_READ)
        assert set(calls) <= {'/calendars/primary', '/calendars/synthetic-calendar'}
    finally:
        close_system(h, controller, window)


@pytest.mark.parametrize('first_consent', [True, False])
def test_first_connection_keeps_primary_selection_without_manual_refresh(tmp_path, monkeypatch, first_consent):
    result, reader, calls = setup_system(tmp_path)
    app, h, service, controller, window, *_ = result
    service.accounts._verified.clear()
    monkeypatch.setattr('jarvis.ui.accounts.QFileDialog.getOpenFileName', lambda *args: ('client.json', ''))
    if first_consent:
        from jarvis.integrations.google.models import OAuthCallback
        from tests.integrations.google.fakes import FakeFlow, FakeReceiver
        accounts = service.accounts
        added = accounts.add('calendar_read', 'Conexão nova', accounts.inventory()['revision'])
        accounts.change(added['connection'], added['revision'], 'default', True)
        reader, row = accounts.default('calendar_read')
        path = accounts._credential(row)
        manager = accounts.auth_factory('calendar_read', path)
        handle = result[5][path]
        receiver = FakeReceiver()
        monkeypatch.setattr(receiver, 'wait', lambda budget, cancel, state: OAuthCallback(
            authorization_response='http://127.0.0.1/callback?code=synthetic-code', state=state, error=None))
        monkeypatch.setattr(manager, '_oauth_flow_factory', lambda path, scopes, state, redirect: FakeFlow(state, handle))
        monkeypatch.setattr(manager, '_callback_receiver_factory', lambda: receiver)
        monkeypatch.setattr(manager, '_browser_open', lambda url: True)
    try:
        page = window.accounts_page
        window._show_page('Contas e conexões')
        settle(app, lambda: not page.busy)
        writer_before = dict(service.accounts.default('calendar_write')[1])
        page.guided_connect.click()  # Google identity/credentials are synthetic; real Qt/admin flow.
        settle(app, lambda: not page.busy)
        assert calls == [] and not service.config.google_calendar_enabled
        assert 'conectado' in page.account_summary.text()
        page.primary_button.click()
        settle(app, lambda: not page.busy)
        assert calls == ['/calendars/primary']
        assert '1 agenda(s)' in page.calendar_state.text()
        page.calendars.setCurrentIndex(0)
        page.select_button.click()
        settle(app, lambda: not page.busy)
        assert service.accounts.default('calendar_read')[1]['resources'] == {'principal': 'synthetic-calendar'}
        assert service.accounts.default('calendar_write')[1] == writer_before
        assert page.read_enabled.isEnabled() and not service.config.google_calendar_enabled
        assert not service.config.google_calendar_write_enabled
    finally:
        close_system(h, controller, window)


@pytest.mark.parametrize('blocked', ['tool', 'primary', 'canonical'])
def test_primary_setup_respects_policy_without_selecting_resources(tmp_path, blocked):
    result, reader, calls = setup_system(tmp_path)
    app, h, service, controller, window, *_ = result
    try:
        resource = ('tool:google_calendar_list_events' if blocked == 'tool' else
                    scoped_resource('google_calendar_list_events', 'calendar_id',
                                    'primary' if blocked == 'primary' else 'synthetic-calendar'))
        if blocked == 'primary':
            _, revision, valid = service.permissions.read()
            assert valid
            service.permissions.set_rule(resource, 'use', 'block', expected_revision=revision)
        else:
            change(service, resource, 'block')
        with pytest.raises(PermissionError):
            service.dispatch('accounts.primary_calendar', {'connection': reader,
                'revision': service.accounts.inventory()['revision']})
        assert service.accounts.default('calendar_read')[1]['resources'] == {}
        assert not service.config.google_calendar_enabled
        assert calls == ([] if blocked != 'canonical' else ['/calendars/primary'])
    finally:
        close_system(h, controller, window)


def test_read_connection_never_implicitly_requests_write_and_events_are_optional(tmp_path):
    result, _, calls = setup_system(tmp_path)
    app, h, service, controller, window, *_ = result
    try:
        page = window.accounts_page
        window._show_page('Contas e conexões')
        settle(app, lambda: not page.busy)
        operations = []
        page.refresh = lambda: operations.append('read-refresh')
        page.connect_account = lambda: operations.append(page.selected['operation'])
        page.guided_connect.click()
        assert operations == ['read-refresh']
        page.show_events.click()
        assert not page.event_options.isHidden() and calls == []
        page.write_connect.click()
        assert operations == ['read-refresh', 'calendar_write']
    finally:
        close_system(h, controller, window)


@pytest.mark.parametrize('failure', ['stale', 'unverified', 'changed_during_request', 'extra_argument'])
def test_primary_metadata_fails_closed_for_stale_identity_and_invalid_schema(tmp_path, failure):
    result, reader, calls = setup_system(tmp_path)
    app, h, service, controller, window, *_ = result
    try:
        arguments = {'connection': reader, 'revision': service.accounts.inventory()['revision']}
        if failure == 'stale':
            arguments['revision'] = 'obsolete'
        elif failure == 'unverified':
            service.accounts._verified.clear()
        elif failure == 'extra_argument':
            arguments['url'] = 'https://example.invalid'
        else:
            factory = service.accounts.calendar_client_factory

            class Changed:
                def get(self, path, params):
                    response = factory(None).get(path, params)
                    service.accounts.change(reader, service.accounts.inventory()['revision'],
                                            'label', 'Changed connection')
                    return response

            service.accounts.calendar_client_factory = lambda auth: Changed()
        with pytest.raises((PermissionError, ValueError)):
            service.dispatch('accounts.primary_calendar', arguments)
        assert calls == (['/calendars/primary'] if failure == 'changed_during_request' else [])
        assert service.accounts.default('calendar_read')[1]['resources'] == {}
        assert not service.config.google_calendar_enabled
        assert not service.config.google_calendar_write_enabled
    finally:
        close_system(h, controller, window)
