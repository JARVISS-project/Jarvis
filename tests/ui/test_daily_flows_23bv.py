from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle


def test_home_availability_navigation_disconnect_and_recovery(tmp_path, monkeypatch):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        monkeypatch.setattr(h.tool_executor, 'execute', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Tool')))
        monkeypatch.setattr(h.llm, 'generate', lambda *a, **k: (_ for _ in ()).throw(AssertionError('LLM')))
        settle(app, lambda: window.availability_panel.data and not window.availability_panel.busy)
        window._show_page('Início')
        settle(app, lambda: not window.availability_panel.busy)
        panel = window.availability_panel
        assert len(panel.data['availability']) == 7
        assert all(l.textFormat() == Qt.TextFormat.PlainText for l in panel.findChildren(QLabel))
        button = next(b for b in panel.findChildren(QPushButton) if b.text() == 'Abrir Contas e conexões')
        button.click()
        assert window.page_stack.currentWidget() is window.accounts_page
        window.nav_buttons['Ferramentas'].click()
        assert window.page_stack.currentWidget() is window.help_page
        window.apply_text_event({'status': 'disconnected'})
        assert not panel.data and 'DESCONECTADO' in window.runtime_label.text()
        window.apply_text_event({'status': 'ready', 'generation': controller.generation})
        settle(app, lambda: panel.data and not panel.busy)
        assert 'CONECTADO' in window.runtime_label.text()
    finally:
        close_system(h, controller, window)


def test_unavailable_command_explains_pending_action_in_conversation(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        window._text_challenge = ('synthetic-session', 'synthetic-challenge')
        assert window._submit_catalog_command('never sent') is None
        assert window.page_stack.currentWidget() is window.conversation_page
        assert 'confirme ou cancele' in window.conversation_status.text()
        window._text_challenge = None
        window._text_generation = None
        window._submit_catalog_command('never sent')
        assert 'Reabra o JARVIS' in window.conversation_status.text()
    finally:
        close_system(h, controller, window)


def test_routine_history_uses_name_and_portuguese_status_without_internal_ids(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        page = window.routines_page
        page.data = {'rows': [{'id': 'study', 'name': 'Modo estudo'}]}
        assert page.history_text({'id': 'secret-id', 'routine': 'study', 'status': 'interrupted',
                                  'steps': [{'status': 'security_blocked'}]}) == (
            'Modo estudo: interrompida; etapas bloqueada por segurança')
    finally:
        close_system(h, controller, window)
