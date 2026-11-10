"""Real UI/admin/executor; synthetic Calendar boundary, no account or screen."""
from datetime import timezone
from threading import Event

import pytest
from PySide6.QtCore import Qt

from jarvis.integrations.google.calendar.google_provider import GoogleCalendarProvider
from jarvis.integrations.google.calendar.read_tool import GoogleCalendarReadAgendaTool
from jarvis.security.capabilities import Capability, CapabilityPolicy
from tests.integration.test_calendar_read_23ba import NOW, event
from tests.integration.test_administration_23bh import change
from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle


class Boundary:
    def __init__(self):
        self.calls = []
        self.failure = False
        self.items = [event('meeting', '<b>Reunião de produto</b>'),
                      event('day', 'Planejamento', all_day=True)]

    def get(self, path, params):
        self.calls.append((path, params))
        if self.failure:
            raise RuntimeError('private@example.invalid secret-token')
        if path.endswith('/events'):
            return {'items': self.items}
        return {'timeZone': 'America/Sao_Paulo'}


def system(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    boundary = Boundary()
    tool = GoogleCalendarReadAgendaTool(GoogleCalendarProvider(boundary), timezone.utc,
        {'principal': 'primary', 'trabalho': 'team@example.invalid'}, lambda: NOW)
    h.tool_registry.register(tool)
    h.tool_executor.capability_policy = service.capability_policy = CapabilityPolicy({Capability.GOOGLE_CALENDAR_READ: True})
    service.config.google_calendar_enabled = True
    return app, h, service, controller, window, boundary


def open_page(app, window):
    window.nav_buttons['Agenda'].click()
    page = window.calendar_page
    settle(app, lambda: not page.busy)
    return page


def test_table_load_search_literal_all_day_and_periods(tmp_path):
    app, h, service, controller, window, boundary = system(tmp_path)
    try:
        page = open_page(app, window)
        assert window.page_stack.currentWidget() is page
        assert boundary.calls == []  # Opening does not contact Google.
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert len(boundary.calls) == 2 and page.table.rowCount() == 2
        assert page.body.currentWidget() is page.table
        assert page.table.item(0, 1).text() == 'Dia inteiro'
        assert page.table.item(1, 2).text() == '<b>Reunião de produto</b>'
        assert '&lt;b&gt;' in page.table.item(1, 2).toolTip()
        assert '<b>' not in page.table.item(1, 2).toolTip()
        page.table.setCurrentCell(1, 2)
        assert '<b>' in page.details.text() and page.details.textFormat() == Qt.TextFormat.PlainText
        page.search.setText('produto')
        assert page.table.rowCount() == 1
        page.search.setText('inexistente')
        assert page.body.currentWidget() is page.empty and 'resultado' in page.empty_title.text()
        page.search.clear()
        page.period_buttons['tomorrow'].click()
        settle(app, lambda: not page.busy)
        assert len(boundary.calls) == 3  # Timezone metadata is cached by the provider.
        assert boundary.calls[-1][1]['timeMin'] == '2026-10-01T03:00:00+00:00'
        assert h.memory.get_history() == []
        audit = (tmp_path / 'audit.log').read_text()
        assert 'produto' not in audit and 'team@example.invalid' not in audit
        page.calendars.setCurrentIndex(page.calendars.findData('trabalho'))
        assert page.table.rowCount() == 0 and page.count_value.text() == '—'
        assert len(boundary.calls) == 3
    finally:
        close_system(h, controller, window)


@pytest.mark.parametrize('blocked', ['capability', 'disabled', 'tool', 'resource', 'ask'])
def test_no_calendar_calls_without_existing_read_authority(tmp_path, blocked):
    app, h, service, controller, window, boundary = system(tmp_path)
    try:
        if blocked == 'capability':
            service.capability_policy = h.tool_executor.capability_policy = CapabilityPolicy({})
        elif blocked == 'disabled':
            service.config.google_calendar_enabled = False
        elif blocked in {'tool', 'ask'}:
            change(service, 'tool:google_calendar_list_events', 'ask' if blocked == 'ask' else 'block')
        else:
            from jarvis.security.permissions import scoped_resource
            for calendar in ('primary', 'team@example.invalid'):
                change(service, scoped_resource('google_calendar_list_events', 'calendar_id', calendar), 'block')
        page = open_page(app, window)
        assert not page.refresh_button.isEnabled()
        result = service.dispatch('calendar.events', {'alias': 'principal', 'period': 'today'})
        assert result['state'] in {'setup', 'blocked'}
        assert boundary.calls == [] and h.confirmation_sessions.current() is None
    finally:
        close_system(h, controller, window)


def test_empty_failure_retry_disconnect_and_clearing_previous_events(tmp_path):
    app, h, service, controller, window, boundary = system(tmp_path)
    try:
        page = open_page(app, window)
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert page.rows
        boundary.failure = True
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert not page.rows and page.count_value.text() == '—'
        assert 'Nenhum compromisso' not in page.empty_title.text()
        assert 'secret-token' not in page.status.text()
        boundary.failure = False
        boundary.items = []
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert page.count_value.text() == '0' and 'Nenhum compromisso' in page.empty_title.text()
        window.apply_text_event({'type': 'text', 'status': 'disconnected'})
        assert not page.refresh_button.isEnabled() and not page.rows
    finally:
        close_system(h, controller, window)


def test_service_rejects_unknown_alias_period_and_extra_arguments(tmp_path):
    app, h, service, controller, window, boundary = system(tmp_path)
    try:
        assert service.dispatch('calendar.events', {'alias': 'not-selected', 'period': 'today'})['state'] != 'loaded'
        for values in ({'alias': 'principal', 'period': 'date:2026-10-01'},
                       {'alias': 'principal', 'period': 'today', 'calendar_id': 'evil'}):
            with pytest.raises(ValueError):
                service.dispatch('calendar.events', values)
        assert boundary.calls == []
    finally:
        close_system(h, controller, window)


def test_scoped_block_keeps_only_the_other_authorized_calendar(tmp_path):
    app, h, service, controller, window, boundary = system(tmp_path)
    try:
        from jarvis.security.permissions import scoped_resource
        change(service, scoped_resource('google_calendar_list_events', 'calendar_id', 'primary'), 'block')
        page = open_page(app, window)
        assert page.calendars.count() == 1 and page.calendars.currentData() == 'trabalho'
        assert service.dispatch('calendar.events', {'alias': 'principal', 'period': 'today'})['state'] == 'blocked'
        assert boundary.calls == []
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert page.rows and all('team%40example.invalid' in path for path, _ in boundary.calls)
    finally:
        close_system(h, controller, window)


def test_table_limit_and_executor_revalidation_after_page_open(tmp_path):
    app, h, service, controller, window, boundary = system(tmp_path)
    try:
        page = open_page(app, window)
        boundary.items = [event(str(i), 'x' * 500) for i in range(20)]
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert len(page.rows) == 20 and all(len(row['title']) == 160 for row in page.rows)
        assert 'Limite' in page.status.text()
        assert not any('id' in row or 'description' in row for row in page.rows)
        calls = len(boundary.calls)
        change(service, 'tool:google_calendar_list_events', 'block')
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert len(boundary.calls) == calls and not page.rows
    finally:
        close_system(h, controller, window)


def test_slow_read_keeps_ui_responsive_and_ignores_duplicate_click(tmp_path):
    app, h, service, controller, window, boundary = system(tmp_path)
    entered, release = Event(), Event()
    original = boundary.get
    def slow(path, params):
        if path.endswith('/events'):
            entered.set()
            assert release.wait(3)
        return original(path, params)
    boundary.get = slow
    try:
        page = open_page(app, window)
        page.refresh_button.click()
        assert entered.wait(2)
        assert page.busy and not page.refresh_button.isEnabled()
        page.load_events()
        page.search.setText('produto')
        app.processEvents()
        assert page.search.text() == 'produto'
        release.set()
        settle(app, lambda: not page.busy)
        assert len([path for path, _ in boundary.calls if path.endswith('/events')]) == 1
        assert page.table.rowCount() == 1 and page.body.currentWidget() is page.table
        window._show_page('Conversa')
        assert not window.conversation_session_toggle.isChecked()
        assert not window.conversation_details_toggle.isChecked()
        window.conversation_details_toggle.click()
        assert window.conversation_details_toggle.isChecked()
    finally:
        release.set()
        close_system(h, controller, window)
