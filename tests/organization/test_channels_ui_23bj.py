import json
import os
from threading import Event, enumerate as threads
from uuid import uuid4

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel

from jarvis.application.administration import AdministrationController
from jarvis.application.text_interaction import TextInteractionController
from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.memory.conversation import ConversationMemory
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.organization import OrganizationExecuteTool, OrganizationUndoTool
from jarvis.ui.qt_app import ControlCenterWindow
from tests.integration.test_administration_23bh import NoLLM
from tests.organization.test_organization_23bj import system, grant
from tests.ui.test_administrative_widgets_23bh import settle


def build(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    registry = ToolRegistry()
    registry.register(OrganizationExecuteTool(service))
    registry.register(OrganizationUndoTool(service))
    config = JarvisConfig(settings_path=tmp_path / 'settings.json', database_path=tmp_path / 'memory.db',
        skill_store_path=tmp_path / 'skills.json', audit_log_path=tmp_path / 'audit.log',
        filesystem_base_directory=tmp_path, obsidian_enabled=False, skills_obsidian_mirror_enabled=False)
    h = Harness(NoLLM(), registry, executor, ConversationMemory(), config=config, organization=service)
    return h, root, selected, service, permissions


@pytest.mark.parametrize('mode', ['text', 'voice'])
def test_same_harness_policy_commands_and_no_provider(tmp_path, mode):
    h, root, selected, service, permissions = build(tmp_path)
    try:
        (root / 'a.txt').write_bytes(b'private')
        assert 'Downloads' in h.processar_mensagem('Organize minha pasta Downloads.', response_mode=mode)
        preview = json.loads(h.processar_mensagem('Organize a pasta selecionada.', response_mode=mode))['plan']
        assert not (root / 'Documentos').exists()
        grant(service, selected, mode='block')
        h.processar_mensagem('/organization execute ' + preview['id'], response_mode=mode)
        assert (root / 'a.txt').exists()
        grant(service, selected)
        preview = service.analyze(selected)
        result = h.processar_mensagem('/organization execute ' + preview['id'], response_mode=mode)
        assert json.loads(result)['completed'] == 1
        assert json.loads(h.processar_mensagem('Mostre o plano de organização.'))['id'] == preview['id']
        grant(service, selected, 'organization_undo')
        assert json.loads(h.processar_mensagem('Desfaça a última organização.'))['undone'] == 1
        assert (root / 'a.txt').read_bytes() == b'private'
    finally:
        h.close_confirmation_sessions()


@pytest.mark.parametrize('outcome', ['confirm', 'cancel', 'revoke', 'wrong', 'expire', 'changed'])
def test_confirmation_binding_single_effect_and_replay(tmp_path, outcome):
    h, root, selected, service, permissions = build(tmp_path)
    try:
        (root / 'a.txt').write_bytes(b'a')
        plan = service.analyze(selected)
        response = h.processar_mensagem('/organization execute ' + plan['id'])
        context = h.current_confirmation_context()
        assert context and 'a.txt' in response and not (root / 'Documentos').exists()
        if outcome == 'cancel':
            h.processar_mensagem('não', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
        else:
            if outcome == 'revoke':
                h.administration.dispatch('organization.grant', {'root': selected, 'action': 'organization_execute',
                    'mode': 'block', 'revision': permissions.read()[1]})
                assert h.current_confirmation_context() is None
            elif outcome == 'expire':
                expires_at = h.confirmation_sessions.current().expires_at
                h.confirmation_sessions.clock = lambda: expires_at + 1
            elif outcome == 'changed':
                (root / 'a.txt').write_bytes(b'changed')
            supplied = (context[0], uuid4().hex) if outcome == 'wrong' else context
            h.processar_mensagem('sim', confirmation_session_id=supplied[0], confirmation_challenge_id=supplied[1])
        assert (root / 'Documentos/a.txt').exists() == (outcome == 'confirm')
        if outcome == 'confirm':
            h.processar_mensagem('sim', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
            assert (root / 'Documentos/a.txt').read_bytes() == b'a'
    finally:
        h.close_confirmation_sessions()


@pytest.mark.parametrize('command', ['Não organize minha pasta Downloads.', 'Organize minha pasta Downloads e apague tudo.',
    '/organization execute C:\\private', '/organization execute ' + 'a' * 32 + ' extra'])
def test_negation_extra_and_paths_have_no_effect(tmp_path, command):
    h, root, selected, service, permissions = build(tmp_path)
    try:
        (root / 'a.txt').write_bytes(b'a')
        h.processar_mensagem(command)
        assert (root / 'a.txt').exists() and not (root / 'Documentos').exists()
    finally:
        h.close_confirmation_sessions()


class Relay(QObject):
    event = Signal(object)


def widgets(tmp_path):
    app = QApplication.instance() or QApplication([])
    h, root, selected, service, permissions = build(tmp_path)
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
    return app, h, root, selected, service, permissions, text, admin, relay, window


def close(h, text, admin, window):
    text.close()
    admin.close()
    h.close_confirmation_sessions()
    window.on_command = None
    window.close()
    assert not any(t.name in {'jarvis-ui-admin', 'jarvis-ui-text'} for t in threads())


def test_real_widgets_selector_preview_confirm_search_history_undo(tmp_path, monkeypatch):
    app, h, root, selected, service, permissions, text, admin, relay, window = widgets(tmp_path)
    try:
        (root / 'a.txt').write_bytes(b'private bytes')
        page = window.organization_page
        window.nav_buttons['Organização de arquivos'].click()
        settle(app, lambda: not page.busy)
        monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(root))
        page.choose_button.click()
        settle(app, lambda: not page.busy)
        assert page.data['folder'] == str(root)
        page.analyze_button.click()
        settle(app, lambda: not page.busy)
        assert 'a.txt' in page.preview.toPlainText() and not (root / 'Documentos').exists()
        page.execute_button.click()
        settle(app, lambda: window._text_challenge is not None)
        assert not (root / 'Documentos').exists()
        window.conversation_confirm.click()
        settle(app, lambda: page.pending is None and not page.busy and not window._text_busy)
        assert (root / 'Documentos/a.txt').read_bytes() == b'private bytes'
        assert 'completed' in page.preview.toPlainText()
        page.search.setText('a.txt')
        page.search_index()
        settle(app, lambda: not page.busy)
        assert 'Documentos/a.txt' in page.index.toPlainText()
        page.list_history_button.click()
        settle(app, lambda: not page.busy)
        assert page.history.count() == 2
        page.history.setCurrentIndex(1)
        page.select_history(1)
        settle(app, lambda: not page.busy)
        page.undo_button.click()
        settle(app, lambda: window._text_challenge is not None)
        window.conversation_confirm.click()
        settle(app, lambda: page.pending is None and not page.busy and not window._text_busy)
        assert (root / 'a.txt').read_bytes() == b'private bytes'
        assert all(label.textFormat() == Qt.TextFormat.PlainText for label in page.findChildren(QLabel))
        window.apply_text_event({'status': 'disconnected'})
        assert page.plan is None and page.history.count() == 0 and page.pending is None
        assert 'a.txt' not in (tmp_path / 'audit.log').read_text()
    finally:
        close(h, text, admin, window)


def test_real_widgets_responsive_cancel_before_effect(tmp_path):
    app, h, root, selected, service, permissions, text, admin, relay, window = widgets(tmp_path)
    entered, release = Event(), Event()
    native = service.backend.move
    def delayed(*args):
        entered.set()
        assert release.wait(2)
        return native(*args)
    service.backend.move = delayed
    try:
        (root / 'a.txt').write_bytes(b'a')
        grant(service, selected)
        page = window.organization_page
        window.nav_buttons['Organização de arquivos'].click()
        settle(app, lambda: not page.busy)
        page.analyze_button.click()
        settle(app, lambda: not page.busy)
        page.execute_button.click()
        settle(app, entered.is_set)
        assert page.pending is not None and not page.execute_button.isEnabled()
        # Qt processes navigation while the actual worker is suspended at the effect boundary.
        window.nav_buttons['Organização de arquivos'].click()
        app.processEvents()
        assert window.page_stack.currentWidget() is page
        page.cancel_button.click()
        settle(app, service._cancel.is_set)
        release.set()
        settle(app, lambda: page.pending is None and not page.busy and not window._text_busy)
        assert (root / 'a.txt').exists() and not (root / 'Documentos/a.txt').exists()
    finally:
        release.set()
        close(h, text, admin, window)


def test_real_widgets_cancel_pending_confirmation_and_stale_generation(tmp_path):
    app, h, root, selected, service, permissions, text, admin, relay, window = widgets(tmp_path)
    try:
        (root / 'a.txt').write_bytes(b'a')
        page = window.organization_page
        window.nav_buttons['Organização de arquivos'].click()
        settle(app, lambda: not page.busy)
        page.analyze_button.click()
        settle(app, lambda: not page.busy)
        page.execute_button.click()
        settle(app, lambda: window._text_challenge is not None)
        pending, challenge = page.pending, window._text_challenge
        window.nav_buttons['Organização de arquivos'].click()
        page.cancel_button.click()
        settle(app, lambda: page.pending is None and not page.busy and not window._text_busy)
        assert h.current_confirmation_context() is None and (root / 'a.txt').exists()
        text.handle('owner', {'command': 'text_confirm', 'generation': pending[0], 'interaction': pending[1],
            'session': challenge[0], 'challenge': challenge[1]}, lambda _event: None)
        text.handle('owner', {'command': 'text_submit', 'generation': uuid4().hex, 'interaction': uuid4().hex,
            'text': '/organization execute ' + page.plan['id']}, lambda _event: None)
        assert not (root / 'Documentos').exists()
        literal = json.loads(json.dumps(page.plan))
        literal['operations'][0]['source'] = '<img src=file:///private>'
        page.show_plan(literal)
        assert '<img src=file:///private>' in page.preview.toPlainText()
    finally:
        close(h, text, admin, window)
