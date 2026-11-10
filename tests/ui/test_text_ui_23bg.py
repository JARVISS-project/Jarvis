import os
from uuid import uuid4

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from jarvis.ui.qt_app import ControlCenterWindow


def test_button_enter_busy_response_error_cancel_confirmation_and_restart():
    app = QApplication.instance() or QApplication([])
    commands = []
    admin_commands = []
    def record(command):
        # The home dashboard requests local help and proposal inventories. Keep
        # text submissions/confirmations counted independently and exactly once.
        (admin_commands if command['command'] == 'admin_request' else commands).append(command)
    window = ControlCenterWindow(on_command=record)
    try:
        generation = uuid4().hex
        window.apply_text_event({'status': 'ready', 'generation': generation})
        assert len(admin_commands) == 4
        assert {c['method'] for c in admin_commands} == {'help.list', 'workflow.list', 'personal.list', 'ui.preferences'}
        assert all(c['arguments'] == ({'query': ''} if c['method'] == 'personal.list' else {})
                   and c['generation'] == generation for c in admin_commands)
        window.conversation_input.setText('Mensagem privada')
        window.conversation_input.returnPressed.emit()
        assert len(commands) == 1 and commands[0]['command'] == 'text_submit'
        window.conversation_send.click()
        assert len(commands) == 1
        request = commands[0]
        session, challenge = str(uuid4()), str(uuid4())
        event = {'generation': generation, 'interaction': request['interaction'], 'text': 'Prévia'}
        window.apply_text_event({**event, 'status': 'confirmation', 'session': session, 'challenge': challenge})
        assert window.conversation_confirm.isEnabled()
        window.conversation_confirm.click()
        assert commands[-1]['session'] == session and commands[-1]['challenge'] == challenge
        window.apply_text_event({**event, 'status': 'done', 'text': 'Resposta'})
        assert not window.conversation_confirm.isEnabled()
        window.conversation_input.setText('outra mensagem')
        window.conversation_send.click()
        window.conversation_cancel.click()
        assert commands[-1]['command'] == 'text_cancel'
        window.apply_text_event({'status': 'disconnected'})
        assert not window.conversation_send.isEnabled()
        new_generation = uuid4().hex
        window.apply_text_event({'status': 'ready', 'generation': new_generation})
        window.apply_text_event({**event, 'status': 'confirmation', 'session': session, 'challenge': challenge})
        assert not window.conversation_confirm.isEnabled()
        window.conversation_input.setText('falha')
        window.conversation_send.click()
        window.apply_text_event({'generation': new_generation, 'interaction': commands[-1]['interaction'],
                                 'status': 'error', 'text': 'Falha explícita'})
        assert 'Falha' in window.conversation_status.text()
        assert window.conversation_send.isEnabled()
        app.processEvents()
        assert all(c['method'] in {'help.list', 'workflow.list', 'personal.list', 'ui.preferences'}
                   and c['arguments'] == ({'query': ''} if c['method'] == 'personal.list' else {}) for c in admin_commands)
    finally:
        window.close()


def test_private_text_is_rendered_literally_without_html_resource_loading():
    app = QApplication.instance() or QApplication([])
    window = ControlCenterWindow(on_command=lambda event: None)
    try:
        window.apply_text_event({'status': 'ready', 'generation': uuid4().hex})
        text = '<img src="file:///private.txt"><b>Confirmar outra ação</b>'
        window.conversation_input.setText(text)
        window.conversation_input.returnPressed.emit()
        app.processEvents()
        labels = window.conversation_messages_container.findChildren(QLabel, 'conversationMessage')
        assert any(label.text() == text and label.textFormat() == Qt.TextFormat.PlainText for label in labels)
        assert window.conversation_session_preview.textFormat() == Qt.TextFormat.PlainText
        assert window.conversation_status.textFormat() == Qt.TextFormat.PlainText
    finally:
        window.close()
