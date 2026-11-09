"""Guided Calendar uses real Qt/admin/accounts; only Google HTTP is fake."""
from threading import enumerate as threads

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import QLabel, QTabWidget

from tests.ui.test_accounts_widgets_23bk import system, close_system, settle


def open_page(tmp_path):
    result = system(tmp_path)
    app, _, _, _, window, *_ = result
    window.nav_buttons['Contas e conexões'].click()
    settle(app, lambda: not window.accounts_page.busy)
    return result


def test_guided_auto_load_real_resource_select_review_no_write_or_oauth(tmp_path):
    app, h, service, controller, window, handles, _ = open_page(tmp_path)
    sent = []
    try:
        page = window.accounts_page
        page.submit_command = sent.append
        tabs = page.findChild(QTabWidget)
        assert tabs.tabText(0) == 'Agenda' and tabs.tabText(1) == 'Gerenciar contas'
        assert page.calendars.count() == 1 and page.calendars.currentIndex() == -1
        assert 'Escolha uma agenda' in page.calendars.placeholderText()
        assert page.calendars.isEnabled() and '1 agenda(s)' in page.calendar_state.text()
        assert 'Agenda de teste' in page.calendars.itemText(0)
        assert 'permite escrita' in page.calendars.itemText(0)
        assert page.data['matching_writer'] and 'Calendar — leitura' in page.account_summary.text()
        assert 'google.sub:' not in page.account_summary.text()
        assert any('synthetic-user' in row['account'] for row in page.data['rows'])
        assert not page.select_button.isEnabled() and not page.review_button.isEnabled()
        page.calendars.setCurrentIndex(0)
        assert page.start.dateTime() > QDateTime.currentDateTime()
        assert page.start.dateTime().secsTo(page.end.dateTime()) == 1800
        page.alias.setText('nome digitado não é uma agenda')
        page.select_button.click()
        settle(app, lambda: not page.busy)
        assert service.accounts.default('calendar_write')[1]['resources'] == {'teste': 'synthetic-calendar'}
        assert page.calendars.currentData() == 'synthetic-calendar'
        assert not service.config.google_calendar_write_enabled
        page.review_button.click()
        assert 'Agenda de teste' in page.preview.text() and 'America/Sao_Paulo' in page.preview.text()
        assert 'Sem convidados' in page.preview.text() and 'nenhuma escrita' in page.status.text()
        assert 'Duração: 30 minutos' in page.preview.text()
        assert page.start.dateTime().time().second() == 0
        page.prepare()
        assert sent == [] and not page.prepare_button.isEnabled()
        page.guided_connect.click()  # valid connection is tested, never a new OAuth.
        settle(app, lambda: not page.busy)
        assert page.data['matching_writer']
        assert all(handle.revoke_calls == 0 for handle in handles.values())
        assert all(label.textFormat() == Qt.TextFormat.PlainText for label in
                   (page.preview, page.account_summary, page.calendar_state, page.timezone))
        audit = h.config.audit_log_path.read_text() if h.config.audit_log_path.exists() else ''
        assert 'synthetic-user' not in audit and 'Agenda de teste' not in audit
    finally:
        close_system(h, controller, window)
        assert not any(t.name == 'jarvis-ui-admin' for t in threads())


def test_friendly_dates_day_change_invalid_interval_literal_and_executor_command(tmp_path):
    app, h, service, controller, window, *_ = open_page(tmp_path)
    sent = []
    try:
        page = window.accounts_page
        page.submit_command = sent.append
        page.calendars.setCurrentIndex(0)
        page.select_button.click()
        settle(app, lambda: not page.busy)
        zone = page.start.timeZone()
        page.start.setDateTime(QDateTime.fromString('2026-10-02T23:55:00-03:00', Qt.DateFormat.ISODate).toTimeZone(zone))
        page.end.setDateTime(page.start.dateTime().addSecs(600))
        page.title.setText('<img src="https://example.invalid/private">')
        page.review()
        assert '03/10/2026' in page.preview.text() and '<img' in page.preview.text()
        assert page.preview.textFormat() == Qt.TextFormat.PlainText
        page.end.setDateTime(page.start.dateTime())
        page.review()
        assert 'posterior' in page.status.text()
        page.data['write_enabled'] = True  # synthetic boundary; backend is still false.
        page.prepare()
        assert sent == []
        page.end.setDateTime(page.start.dateTime().addSecs(600))
        page.prepare()
        assert len(sent) == 1 and sent[0].startswith('/agenda criar ')
        import json
        args = json.loads(sent[0].split(' ', 2)[2])
        assert args['calendario'] == 'teste' and args['timezone'] == 'America/Sao_Paulo'
        assert args['inicio'] == '2026-10-02T23:55:00-03:00'
        assert args['fim'] == '2026-10-03T00:05:00-03:00'
        page.calendars.setCurrentIndex(-1)
        page.prepare()
        assert len(sent) == 1  # saved resource cannot replace current explicit choice.
    finally:
        close_system(h, controller, window)


def test_guided_wrong_identity_never_combines_grants_and_failure_is_sanitized(tmp_path):
    app, h, service, controller, window, *_ = system(tmp_path)
    try:
        service.accounts.identity = lambda auth, op: ('google.sub', 'other' if op == 'calendar_write' else 'reader')
        page = window.accounts_page
        window.nav_buttons['Contas e conexões'].click()
        settle(app, lambda: not page.busy)
        assert not page.data['matching_writer']
        assert 'somente leitura' in page.calendars.itemText(0)
        service.accounts.calendar_client_factory = lambda auth: (_ for _ in ()).throw(RuntimeError('private-token'))
        page.guided_refresh.click()
        settle(app, lambda: not page.busy)
        assert page.calendars.count() == 0
        assert 'Não foi possível' in page.calendar_state.text()
        assert not page.review_button.isEnabled()
        assert not any('private-token' in label.text() for label in page.findChildren(QLabel))
    finally:
        close_system(h, controller, window)


def test_guided_scoped_block_and_exact_schema_no_metadata_request(tmp_path):
    import pytest
    from jarvis.security.permissions import scoped_resource
    from tests.integration.test_administration_23bh import change
    app, h, service, controller, window, *_ = system(tmp_path)
    try:
        class BlockedClient:
            def get(self, *args):
                raise AssertionError('blocked HTTP')
        service.accounts.calendar_client_factory = lambda auth: BlockedClient()
        change(service, scoped_resource('google_calendar_list_events', 'calendar_id', 'synthetic-calendar'), 'block')
        data = service.dispatch('accounts.calendar_guide', {})
        assert data['calendars'] == [] and not data['write_enabled']
        with pytest.raises(ValueError):
            service.dispatch('accounts.calendar_guide', {'scope': 'allow_everything'})
        change(service, 'tool:google_calendar_list_events', 'block')
        with pytest.raises(PermissionError):
            service.dispatch('accounts.calendar_guide', {})
    finally:
        close_system(h, controller, window)


def test_page_opened_before_runtime_ready_loads_automatically_on_handshake(tmp_path):
    app, h, service, controller, window, *_ = system(tmp_path)
    try:
        window.apply_text_event({'type': 'text', 'status': 'disconnected'})
        page = window.accounts_page
        window.nav_buttons['Contas e conexões'].click()
        assert not page.busy and not page.data
        window.apply_text_event({'type': 'text', 'status': 'ready', 'generation': controller.generation})
        settle(app, lambda: not page.busy)
        assert page.calendars.count() == 1 and page.calendars.currentIndex() == -1
        assert 'Escolha' in page.calendars.placeholderText() and page.calendars.isEnabled()
        assert not service.config.google_calendar_write_enabled
    finally:
        close_system(h, controller, window)


def test_empty_agendas_explains_missing_binding_and_missing_timezone_blocks_review(tmp_path):
    app, h, service, controller, window, *_ = system(tmp_path)
    try:
        state = service.accounts.inventory()
        raw = service.accounts._read()
        raw['connections'][raw['defaults']['calendar_read']]['resources'] = {}
        service.accounts._save(raw)
        page = window.accounts_page
        window.nav_buttons['Contas e conexões'].click()
        settle(app, lambda: not page.busy)
        assert page.calendars.count() == 0 and not page.calendars.isEnabled()
        assert 'Nenhuma agenda foi vinculada' in page.calendar_state.text()
        assert 'Nenhuma agenda disponível' in page.calendars.placeholderText()
        page.render_calendars({'calendars': [{'id': 'synthetic-calendar', 'label': 'Sem fuso', 'timezone': ''}]})
        page.calendars.setCurrentIndex(0)
        assert not page.review_button.isEnabled()
        page.review()
        assert 'fuso real' in page.status.text()
    finally:
        close_system(h, controller, window)
