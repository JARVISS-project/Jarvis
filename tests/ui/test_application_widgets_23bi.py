import os
from threading import enumerate as threads

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QLabel

from jarvis.application.administration import AdministrationController
from jarvis.application.text_interaction import TextInteractionController
from jarvis.desktop.application_windows import ApplicationWindow
from jarvis.ui.qt_app import ControlCenterWindow
from tests.integration.test_application_channels_23bi import build
from tests.desktop.test_application_catalog_23bi import allow
from tests.ui.test_administrative_widgets_23bh import settle


class Relay(QObject):
    event = Signal(object)


def widgets(tmp_path):
    app = QApplication.instance() or QApplication([])
    h, entry, service, backend, permissions, tools = build(tmp_path)
    text = TextInteractionController(h)
    admin = AdministrationController(h.administration, text.generation)
    relay = Relay()
    window = ControlCenterWindow()
    relay.event.connect(window.apply_text_event)
    def send(payload):
        if payload['command'] == 'admin_request':
            admin.handle({**payload, 'authorization': admin.authorization}, relay.event.emit)
        else:
            text.handle('owner', payload, relay.event.emit)
    window.on_command = send
    window.administrative_client.send = send
    window.apply_text_event(text.hello())
    return app, h, entry, service, backend, permissions, text, admin, relay, window


def close(h, text, admin, window):
    text.close()
    admin.close()
    h.close_confirmation_sessions()
    window.on_command = None
    window.close()
    assert not any(thread.name in {'jarvis-ui-admin', 'jarvis-ui-text'} for thread in threads())


def test_catalog_real_widgets_admin_grant_preferences_and_executor_open(tmp_path):
    app, h, entry, service, backend, permissions, text, admin, relay, window = widgets(tmp_path)
    try:
        page = window.applications_page
        window.nav_buttons['Aplicativos'].click()
        settle(app, lambda: not page.busy)
        assert window.page_stack.currentWidget() is page and page.rows.count() == 1
        page.rows.setCurrentRow(0)
        assert 'open: block' in page.details.text() and not backend.effects
        page.mode.setCurrentIndex(page.mode.findData('allow'))
        page.apply_button.click()
        settle(app, lambda: not page.busy)
        page.rows.setCurrentRow(0)
        assert service.mode(entry, 'open').mode == 'allow'
        page.aliases.setText('meu aplicativo')
        page.save_button.click()
        settle(app, lambda: not page.busy)
        page.rows.setCurrentRow(0)
        assert page.aliases.text() == 'meu aplicativo'
        page.effect_buttons['open'].click()
        assert window.page_stack.currentWidget() is window.conversation_page
        settle(app, lambda: not window._text_busy and page.pending is None)
        assert len(backend.effects) == 1 and 'verificada' in page.status.text()
        page.remove_button.click()
        settle(app, lambda: not page.busy)
        assert service.mode(entry, 'open').mode == 'block'
        assert len(backend.effects) == 1
        page.search.setText('inexistente')
        assert page.rows.count() == 0 and not page.apply_button.isEnabled()
    finally:
        close(h, text, admin, window)


def test_window_selection_confirmation_cancel_then_close_exactly_selected(tmp_path):
    app, h, entry, service, backend, permissions, text, admin, relay, window = widgets(tmp_path)
    try:
        allow(service, entry, 'status')
        allow(service, entry, 'close')
        backend.instances = [ApplicationWindow(1, 2, 3, 4, 'class', '<img src=file:///private>'), ApplicationWindow(5, 6, 7, 8, 'class', 'another')]
        page = window.applications_page
        window.nav_buttons['Aplicativos'].click()
        settle(app, lambda: not page.busy)
        page.rows.setCurrentRow(0)
        page.effect_buttons['status'].click()
        settle(app, lambda: page.pending is None and not window._text_busy)
        assert page.windows.count() == 3 and page.windows.currentData() is None
        assert '<img src=file:///private>' in page.windows.itemText(1)
        page.windows.setCurrentIndex(2)
        page.effect_buttons['close'].click()
        settle(app, lambda: window._text_challenge is not None)
        assert not backend.effects and window.conversation_confirm.isEnabled()
        window.conversation_cancel.click()
        settle(app, lambda: page.pending is None and not window._text_busy)
        assert not backend.effects
        page.effect_buttons['status'].click()
        settle(app, lambda: page.pending is None and not window._text_busy)
        page.windows.setCurrentIndex(2)
        page.effect_buttons['close'].click()
        settle(app, lambda: window._text_challenge is not None)
        window.conversation_confirm.click()
        settle(app, lambda: page.pending is None and not window._text_busy)
        assert backend.effects == [('close', '5|6|7|8|class')] and len(backend.instances) == 1
        assert page.windows.count() == 1
    finally:
        close(h, text, admin, window)


def test_literal_content_disabled_backend_restart_and_private_error(tmp_path, monkeypatch):
    app, h, entry, service, backend, permissions, text, admin, relay, window = widgets(tmp_path)
    try:
        from dataclasses import replace
        service.catalog.discovery.entries = [replace(entry, name='<img src=file:///private>')]
        page = window.applications_page
        window.nav_buttons['Aplicativos'].click()
        settle(app, lambda: not page.busy)
        page.rows.setCurrentRow(0)
        assert '<img src=file:///private>' in page.details.text()
        assert page.details.textFormat() == Qt.TextFormat.PlainText
        assert all(label.textFormat() == Qt.TextFormat.PlainText for label in page.findChildren(QLabel))
        window.apply_text_event({'status': 'disconnected'})
        assert page.windows.count() == 1 and page.pending is None
        page.refresh_button.click()
        assert not page.busy and 'desconectado' in page.status.text()
        window.apply_text_event(text.hello())
        def broken():
            raise OSError('PRIVATE PATH / SECRET')
        monkeypatch.setattr(service, 'refresh', broken)
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert 'indisponível' in page.status.text() and 'SECRET' not in page.status.text()
        monkeypatch.undo()
        service.enabled = False
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        page.rows.setCurrentRow(0)
        assert not page.apply_button.isEnabled() and not page.effect_buttons['open'].isEnabled()
        assert not backend.effects
    finally:
        close(h, text, admin, window)
