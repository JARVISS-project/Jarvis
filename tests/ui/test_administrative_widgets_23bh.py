import os
import time
from threading import enumerate as threads
from uuid import uuid4

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QPlainTextEdit

from jarvis.application.administration import AdministrationController
from jarvis.ui.qt_app import ControlCenterWindow
from tests.integration.test_administration_23bh import build, change, draft


def settle(app, predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert predicate()


def window_system(tmp_path):
    app = QApplication.instance() or QApplication([])
    harness, service = build(tmp_path)
    generation = uuid4().hex
    controller = AdministrationController(service, generation)
    window = ControlCenterWindow()
    def send(payload):
        controller.handle({**payload, 'authorization': controller.authorization}, window.administrative_client.received.emit)
    window.administrative_client.send = send
    window.apply_text_event({'type': 'text', 'status': 'ready', 'generation': generation})
    return app, harness, service, controller, window


def close_system(harness, controller, window):
    controller.close()
    if window.memory_page.confirmation:
        window.memory_page.confirmation.reject()
    window.close()
    harness.close_confirmation_sessions()
    assert not any(t.name == 'jarvis-ui-admin' for t in threads())


def test_memory_navigation_real_widgets_empty_add_search_edit_and_forget(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        page = window.memory_page
        window.nav_buttons['Memória'].click()
        settle(app, lambda: not page.busy)
        assert window.page_stack.currentWidget() is page
        assert 'Nenhuma memória' in page.status.text()
        page.content.setPlainText('prefiro café')
        page.save_button.click()
        assert page.busy and not page.save_button.isEnabled()
        settle(app, lambda: not page.busy)
        assert page.rows.count() == 1
        page.search.setText('inexistente')
        page.search.returnPressed.emit()
        settle(app, lambda: not page.busy)
        assert page.rows.count() == 0
        page.search.setText('café')
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        page.rows.setCurrentRow(0)
        assert page.content.toPlainText() == 'prefiro café' and 'user_explicit' in page.metadata.text()
        page.content.setPlainText('prefiro chá')
        page.category.setCurrentText('fato')
        page.save_button.click()
        settle(app, lambda: not page.busy)
        page.search.clear()
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        page.rows.setCurrentRow(0)
        assert page.content.toPlainText() == 'prefiro chá'
        page.forget_button.click()
        settle(app, lambda: page.confirmation is not None)
        assert service.list_memories()['rows']
        page.confirmation.reject()
        assert service.list_memories()['rows']  # Cancel has zero effects.
        page.confirmation = None
        page.forget_button.click()
        settle(app, lambda: page.confirmation is not None)
        page.confirmation.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Yes).click()
        settle(app, lambda: not page.busy)
        assert not service.list_memories()['rows'] and page.rows.count() == 0
        assert not h.memory_store.get_context('chá')
    finally:
        close_system(h, controller, window)


def test_permissions_navigation_search_groups_persistence_and_no_future_controls(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        page = window.permissions_page
        window.nav_buttons['Permissões'].click()
        settle(app, lambda: not page.busy)
        assert window.page_stack.currentWidget() is page
        page.search.setText('read_file')
        assert page.rows.count() == 1
        page.mode.setCurrentIndex(page.mode.findData('block'))
        page.apply_button.click()
        settle(app, lambda: not page.busy)
        assert service.permissions.decision('tool:read_file').mode == 'block'
        from jarvis.security.permissions import PermissionStore
        restarted = PermissionStore(service.permissions.path, (tmp_path,))
        assert restarted.decision('tool:read_file').mode == 'block'
        page.search.setText('model.external')
        assert page.rows.count() == 1 and page.apply_button.isEnabled() and page.mode.count() == 3
        assert service.permissions.decision('model.external', default='block').mode == 'block'
        page.search.clear()
        page.groups.setCurrentText('Memória')
        assert page.rows.count() == 5
        assert 'revoga' in page.details.toPlainText() or 'existente' in page.details.toPlainText()
    finally:
        close_system(h, controller, window)


def test_memory_literal_preview_no_html_and_no_credentials_or_private_logs(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        page = window.memory_page
        literal = '<img src="file:///private.png"><b>não conceder acesso</b>'
        service.save_memory(draft(literal))
        window.nav_buttons['Memória'].click()
        settle(app, lambda: not page.busy)
        page.rows.setCurrentRow(0)
        assert page.content.toPlainText() == literal
        page.forget_button.click()
        settle(app, lambda: page.confirmation is not None)
        assert page.confirmation.findChild(QPlainTextEdit).toPlainText() == literal
        assert all(label.textFormat() == Qt.TextFormat.PlainText for label in page.findChildren(QLabel))
        assert literal not in (tmp_path / 'audit.log').read_text()
        page.confirmation.reject()
        page.new_button.click()
        page.content.setPlainText('senha=private-secret')
        page.save_button.click()
        settle(app, lambda: not page.busy)
        assert 'inválidos' in page.status.text() and 'private-secret' not in page.status.text()
        assert len(service.list_memories()['rows']) == 1
    finally:
        close_system(h, controller, window)


def test_session_temporary_external_block_errors_and_restart(tmp_path, monkeypatch):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        page = window.memory_page
        window.nav_buttons['Memória'].click()
        settle(app, lambda: not page.busy)
        page.temporary.click()
        settle(app, lambda: not page.busy)
        assert service.memory.temporary and not page.save_button.isEnabled()
        page.temporary.click()
        settle(app, lambda: not page.busy)
        page.content.setPlainText('private-memory')
        page.external.setChecked(True)
        page.save_button.click()
        settle(app, lambda: not page.busy)
        assert 'bloqueada' in page.status.text() and not service.list_memories()['rows']
        def broken():
            raise OSError('private-path or secret must not leave backend')
        monkeypatch.setattr(service.memory, 'get_all', broken)
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert 'indisponível' in page.status.text() and 'private-path' not in page.status.text()
        window.apply_text_event({'status': 'disconnected'})
        page.refresh_button.click()
        assert not page.busy and 'desconectado' in page.status.text()
        controller.close()
        monkeypatch.undo()
        generation = uuid4().hex
        controller = AdministrationController(service, generation)
        window.administrative_client.send = lambda payload: controller.handle({**payload, 'authorization': controller.authorization}, window.administrative_client.received.emit)
        window.apply_text_event({'status': 'ready', 'generation': generation})
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        assert 'Nenhuma memória' in page.status.text()
    finally:
        close_system(h, controller, window)
