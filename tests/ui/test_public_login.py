from jarvis.integrations.google.public_login import PUBLIC_CLIENT
from tests.ui.test_accounts_widgets_23bk import system
from tests.ui.test_administrative_widgets_23bh import close_system


def test_connect_uses_packaged_identity_only_for_supported_operation(tmp_path, monkeypatch):
    app, h, service, controller, window, *_ = system(tmp_path)
    try:
        page = window.accounts_page
        assert callable(page.event)  # QWidget's event dispatcher must remain callable.
        assert page.event_id.text() == ''
        page.data = service.accounts.inventory()
        page.data['google_login'] = {'available': True, 'mode': 'testing', 'operations': ['calendar_read']}
        page.selected = next(r for r in page.data['rows'] if r['operation'] == 'calendar_read')
        page.selected = {**page.selected, 'status': 'não conectado'}
        requests = []
        page.account_request = lambda method, extra=None: requests.append((method, extra))
        monkeypatch.setattr('jarvis.ui.accounts.QFileDialog.getOpenFileName',
                            lambda *a: (_ for _ in ()).throw(AssertionError('No technical file picker')))
        page.connect_button.setEnabled(True)
        page.connect_button.click()
        assert requests == [('accounts.connect', {'client_config': PUBLIC_CLIENT})]
        page.selected = {**page.selected, 'status': 'conectado'}
        page.connect_account()
        assert requests[-1] == ('accounts.test', None)
        page.selected = {**page.selected, 'operation': 'calendar_write', 'status': 'não conectado'}
        monkeypatch.setattr('jarvis.ui.accounts.QFileDialog.getOpenFileName', lambda *a: ('', ''))
        page.connect_account()
        assert len(requests) == 2  # No automatic write consent or fallback to the public client.
        data = service.accounts.inventory()
        data['google_login'] = {'available': True, 'mode': 'testing', 'operations': ['calendar_read']}
        page.render(data)
        assert 'Login JARVIS incluído' in page.setup_help.text()
        assert 'somente contas cadastradas para teste' not in page.setup_help.text()
        assert 'avisos e limites' in page.setup_help.text()
        data['google_login']['available'] = False
        page.render(data)
        assert 'arquivo OAuth' in page.setup_help.text()
    finally:
        close_system(h, controller, window)


def test_guided_read_selection_matches_default_query_alias_without_affecting_write(tmp_path):
    app, h, service, controller, window, *_ = system(tmp_path)
    try:
        page = window.accounts_page
        data = service.accounts.inventory()
        reader = next(r for r in data['rows'] if r['operation'] == 'calendar_read')
        writer = next(r for r in data['rows'] if r['operation'] == 'calendar_write')
        data.update(candidate=reader['id'], matching_writer=writer['id'],
            calendars=[{'id': 'synthetic-calendar', 'label': 'Agenda escolhida', 'write_candidate': True}],
            calendar_notice='Escolha explícita de teste.')
        page.render(data)
        assert 'google.sub:' not in page.account_summary.text()
        page.calendars.setCurrentIndex(0)
        requests = []
        page.request = lambda method, arguments, callback: requests.append((method, arguments))
        page.select_calendar()
        assert requests[-1][1]['alias'] == 'principal'
        assert requests[-1][1]['write_connection'] is None
        page.select_calendar(for_events=True)
        assert requests[-1][1]['alias'] == 'teste'
        assert requests[-1][1]['write_connection'] == writer['id']
        assert not service.config.google_calendar_write_enabled
    finally:
        close_system(h, controller, window)


def test_pending_login_explains_browser_and_retry_requires_new_user_action(tmp_path, monkeypatch):
    app, h, service, controller, window, *_ = system(tmp_path)
    try:
        page = window.accounts_page
        page.data = service.accounts.inventory()
        page.selected = next(r for r in page.data['rows'] if r['operation'] == 'calendar_read')
        pending = []
        monkeypatch.setattr(page.client, 'submit', lambda method, args, callback: pending.append((method, args, callback)))
        flags = (service.config.google_calendar_enabled, service.config.google_calendar_write_enabled)
        page.account_request('accounts.connect', {'client_config': PUBLIC_CLIENT})
        assert page.busy and len(pending) == 1
        assert 'navegador' in page.status.text() and '10 minutos' in page.status.text()
        page.account_request('accounts.connect', {'client_config': PUBLIC_CLIENT})
        assert len(pending) == 1  # Double activation cannot start concurrent consent.
        pending[0][2]({'status': 'error', 'error': 'O prazo do login Google expirou.'})
        assert not page.busy and len(pending) == 1  # No automatic retry.
        assert 'expirou' in page.status.text()
        page.account_request('accounts.connect', {'client_config': PUBLIC_CLIENT})
        assert page.busy and len(pending) == 2
        assert (service.config.google_calendar_enabled, service.config.google_calendar_write_enabled) == flags
    finally:
        close_system(h, controller, window)
