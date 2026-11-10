from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from jarvis.ui.workbench import CommandPalette
from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle
from tests.application.test_calendar_workflow import system
from tests.ui.test_calendar_workflow_widgets import own_session


def test_commands_match_accents_and_only_open_contact():
    app = QApplication.instance() or QApplication([])
    calls = []
    class Client:
        def submit(self, method, arguments, callback):
            assert method == 'contacts.list' and arguments == {}
            callback({'status': 'done', 'data': {'rows': [
                {'id': 'synthetic', 'name': 'João', 'email': 'joao@example.invalid', 'whatsapp': ''}]}})
    palette = CommandPalette(Client(), [('Contatos', lambda id=None: calls.append(id), ''),
                                       ('Próximo lembrete', lambda: calls.append('reminder'), '')])
    try:
        palette.search.setText('joao')
        assert palette.rows.count() == 1
        assert not calls
        palette.activate(palette.rows.item(0))
        assert calls == ['synthetic']
    finally:
        palette.close()


def test_theme_preferences_shortcuts_and_personal_capture_round_trip(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        settle(app, lambda: window._preferences is not None and not window.personal_panel.busy)
        window._show_page('Preferências')
        page = window.settings_hub
        settle(app, lambda: bool(page.data) and not page.busy)
        page.theme.setCurrentIndex(page.theme.findData('light'))
        page.density.setCurrentIndex(page.density.findData('compact'))
        page.save_button.click()
        settle(app, lambda: not page.busy and window.desktop_theme.is_light())
        assert 'font-size: 13px' in window.styleSheet()
        window.shortcuts['calendar'].activated.emit()
        assert window._active_page_name == 'Agenda'
        window.shortcuts['find'].activated.emit()
        assert window.calendar_page.search.hasSelectedText() is False
        window.shortcuts['quick_task'].activated.emit()
        settle(app, lambda: window.personal_panel.dialog is not None)
        dialog = window.personal_panel.dialog
        assert dialog.isModal() and not service.personal.inventory()['rows']
        window.shortcuts['calendar'].activated.emit()
        assert window._active_page_name == 'Início'  # Modal capture blocks navigation hotkeys.
        dialog.reject()
        assert not service.personal.inventory()['rows']
        for _ in range(10):
            app.processEvents()
        assert not window.desktop_theme.pending.isActive()
    finally:
        if window.personal_panel.dialog:
            window.personal_panel.dialog.reject()
        close_system(h, controller, window)


def test_escape_inline_review_cannot_send_and_navigation_still_works(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, _ = values
    own_session(window, controller)
    try:
        settle(app, lambda: not window.workflow_inbox.busy)
        workflow.scan()
        window.workflow_inbox.refresh()
        settle(app, lambda: not window.workflow_inbox.busy)
        window.workflow_inbox.review(window.workflow_inbox.data['rows'][0])
        surface = window.review_surface
        settle(app, lambda: not surface.page.busy)
        window.shortcuts['calendar'].activated.emit()
        assert window._active_page_name == 'Início'
        window._escape_view()
        assert surface.page.closed and window.review_surface is None
        assert not workflow.adapter.calls
        window.shortcuts['calendar'].activated.emit()
        assert window._active_page_name == 'Agenda'
    finally:
        close_system(h, controller, window)
