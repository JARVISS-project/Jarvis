"""Real widgets/admin worker/policy and protected store, synthetic Google only."""
import os
from threading import enumerate as threads

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle
from tests.integrations.google.test_accounts_23bk import make


def system(tmp_path):
    app, h, service, controller, window = window_system(tmp_path/'runtime')
    accounts, handles, managers = make(tmp_path/'accounts')
    service.accounts = accounts
    service.config.google_calendar_enabled = True
    class CalendarClient:
        def get(self, path, params):
            assert path in {'/calendars/synthetic-calendar', '/calendars/synthetic-calendar/acl'}
            if path.endswith('/acl'):
                return {'etag': 'private-version', 'items': [{'role': 'owner', 'scope': {'type': 'user'}}]}
            return {'id': 'synthetic-calendar', 'summary': 'Agenda de teste', 'timeZone': 'America/Sao_Paulo'}
    accounts.calendar_client_factory = lambda auth: CalendarClient()
    service.permissions.protected_paths.add(accounts.directory.resolve())
    from datetime import timezone
    from jarvis.integrations.google.calendar.read_tool import GoogleCalendarReadAgendaTool
    from tests.integration.test_google_calendar_runtime import FakeProvider
    h.tool_registry.register(GoogleCalendarReadAgendaTool(FakeProvider(), timezone.utc, {'agenda': 'synthetic-calendar'}))
    return app, h, service, controller, window, handles, managers


def test_accounts_navigation_add_test_label_literal_default_disconnect_and_restart(tmp_path):
    app, h, service, controller, window, handles, _ = system(tmp_path)
    try:
        page = window.accounts_page
        window.nav_buttons['Contas e conexões'].click()
        settle(app, lambda: not page.busy)
        assert window.page_stack.currentWidget() is page and page.rows.count() == 5
        assert not page.prepare_button.isEnabled()
        page.rows.setCurrentRow(0)
        page.test_button.click()
        assert page.busy and not page.test_button.isEnabled()
        settle(app, lambda: not page.busy)
        assert page.selected['status'] == 'conectado'
        page.label.setText('<img src="https://example.invalid/secret">')
        page.rename_button.click()
        settle(app, lambda: not page.busy)
        assert '<img' in page.rows.currentItem().text()
        assert all(label.textFormat() == Qt.TextFormat.PlainText for label in page.findChildren(QLabel)
                   if label in (page.status, page.details, page.calendar_state))
        old_id = page.selected['id']
        page.label.setText('Outra conta explícita')
        page.operation.setCurrentIndex(0)
        page.add_button.click()
        settle(app, lambda: not page.busy)
        assert page.rows.count() == 6
        for i in range(page.rows.count()):
            if page.rows.item(i).data(Qt.ItemDataRole.UserRole)['label'] == 'Outra conta explícita':
                page.rows.setCurrentRow(i)
        assert not page.selected['resources'] and page.selected['status'] == 'não conectado'
        page.default_button.click()
        settle(app, lambda: not page.busy)
        assert page.selected['default'] and page.selected['id'] != old_id
        page.disconnect_button.click()
        assert page.confirmation is not None
        page.confirmation.reject()
        assert page.selected['id']
        page.disconnect_button.click()
        page.confirmation.accept()
        settle(app, lambda: not page.busy)
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert service.accounts.default('calendar_read')[0] != old_id
        assert all(handle.revoke_calls == 0 for handle in handles.values())
        audit = h.config.audit_log_path.read_text() if h.config.audit_log_path.exists() else ''
        assert 'example.invalid' not in audit and 'Outra conta' not in audit and 'refresh-secret' not in audit
        from jarvis.application.administration import AdministrationController
        page.rows.setCurrentRow(0)
        page.disconnect_button.click()
        stale_dialog = page.confirmation
        controller.close()
        controller = AdministrationController(service, 'f'*32)
        page.client.send = lambda payload: controller.handle({**payload, 'authorization': controller.authorization}, page.client.received.emit)
        page.client.apply_event({'status': 'ready', 'generation': 'f'*32})
        stale_dialog.accept()
        assert 'antiga' in page.status.text()
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert page.rows.count() == 6
    finally:
        close_system(h, controller, window)
        assert not any(t.name == 'jarvis-ui-admin' for t in threads())


def test_ui_connection_failure_literal_and_write_false_has_zero_text_requests(tmp_path):
    app, h, service, controller, window, *_ = system(tmp_path)
    sent = []
    try:
        page = window.accounts_page
        window.nav_buttons['Contas e conexões'].click()
        settle(app, lambda: not page.busy)
        page.submit_command = sent.append
        page.prepare()
        assert sent == [] and not page.prepare_button.isEnabled()
        page.rows.setCurrentRow(0)
        service.accounts.identity = lambda *args: (_ for _ in ()).throw(RuntimeError('private-token'))
        page.test_button.click()
        settle(app, lambda: not page.busy)
        assert 'private-token' not in page.status.text() and 'bloqueada' in page.status.text()
        assert not service.accounts.config.google_calendar_write_enabled
        assert not any('private-token' in label.text() for label in page.findChildren(QLabel))
    finally:
        close_system(h, controller, window)


def test_admin_exact_schemas_and_calendar_policy_block(tmp_path):
    app, h, service, controller, window, *_ = system(tmp_path)
    try:
        import pytest
        from tests.integration.test_administration_23bh import change
        data = service.dispatch('accounts.list', {})
        with pytest.raises(ValueError): service.dispatch('accounts.list', {'token': 'bad'})
        with pytest.raises(ValueError): service.dispatch('accounts.read', {'enabled': 'true'})
        change(service, 'tool:google_calendar_list_events', 'block')
        reader = next(r for r in data['rows'] if r['operation'] == 'calendar_read')
        with pytest.raises(PermissionError):
            service.dispatch('accounts.calendars', {'connection': reader['id'], 'revision': data['revision']})
        with pytest.raises(ValueError): service.dispatch('accounts.change', {'connection': reader['id'],
            'revision': data['revision'], 'action': 'allow_everything', 'value': True})
    finally:
        close_system(h, controller, window)


def test_account_test_runs_outside_gui_and_leaves_no_worker(tmp_path):
    from threading import Event
    from PySide6.QtCore import QTimer
    app, h, service, controller, window, *_ = system(tmp_path)
    started, release = Event(), Event()
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(1)
    def identity(*args):
        started.set()
        assert release.wait(2)
        return 'google.sub', 'synthetic-user'
    try:
        page = window.accounts_page
        window.nav_buttons['Contas e conexões'].click()
        settle(app, lambda: not page.busy)
        service.accounts.identity = identity
        page.rows.setCurrentRow(0)
        page.test_button.click()
        settle(app, started.is_set)
        baseline = len(ticks)
        settle(app, lambda: len(ticks) > baseline + 2)
        assert page.busy
        release.set()
        settle(app, lambda: not page.busy)
        assert page.selected['status'] == 'conectado'
    finally:
        release.set()
        timer.stop()
        close_system(h, controller, window)
