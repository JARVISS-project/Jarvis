from threading import Event
from types import SimpleNamespace
import time
from uuid import uuid4

from PySide6.QtCore import QTimer

from jarvis.application.conversation_preferences import ConversationPreferences
from jarvis.application.text_interaction import TextInteractionController
from jarvis.llm.types import LLMResponse
from jarvis.ui.qt_app import _ConversationLogRelay
from tests.application.test_conversation_preferences_23bm import Sink, config_with_voice
from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle


def test_settings_ui_persist_and_preview_only_after_click(tmp_path, monkeypatch):
    config = config_with_voice(tmp_path, monkeypatch)
    app, h, admin, controller, window = window_system(tmp_path)
    sink = Sink()
    speech = ConversationPreferences(config, factory=lambda cfg: sink)
    admin.conversation_preferences = speech
    try:
        window.nav_buttons['Ajustes'].click()
        page = window.preferences_page
        settle(app, lambda: bool(page.data) and not page.busy)
        assert window.page_stack.currentWidget() is page and not sink.calls
        page.name.setText('Pessoa')
        page.tone.setCurrentIndex(page.tone.findData('direto'))
        page.detail.setCurrentIndex(page.detail.findData('detalhadas'))
        page.opinions.setChecked(False)
        page.spoken.setChecked(False)
        page.pace.setCurrentIndex(page.pace.findData('calm'))
        page.save_button.click()
        settle(app, lambda: config.assistant_tone == 'direto' and not page.busy)
        assert window.user_label.text() == 'Pessoa' and not sink.calls
        page.preview_button.click()
        settle(app, lambda: bool(sink.calls) and not page.busy)
        assert len(sink.calls) == 1
        page.stop_button.click()
        settle(app, lambda: sink.stops > 0)
        page.reset_button.click()
        assert page.tone.currentData() == 'equilibrado' and config.assistant_tone == 'direto'
    finally:
        speech.close(); close_system(h, controller, window)


def test_ui_heartbeat_during_generation_and_text_reaches_tts_sink(tmp_path, monkeypatch):
    config = config_with_voice(tmp_path, monkeypatch)
    app, h, admin, admin_controller, window = window_system(tmp_path)
    h.config = h.response_coordinator.config = h.agent_loop_coordinator.config = config
    entered, release = Event(), Event()
    def generate(**kwargs):
        entered.set()
        assert release.wait(3)
        return LLMResponse(content='Minha opinião: pratique com exemplos simples.')
    llm = SimpleNamespace(generate=generate)
    h.llm = h.response_coordinator.llm = h.agent_loop_coordinator.llm = llm
    sink = Sink()
    speech = ConversationPreferences(config, sink)
    text = TextInteractionController(h, speech)
    relay = _ConversationLogRelay(window)
    relay.snapshot_received.connect(window.apply_text_event)
    events = []
    def reply(e):
        events.append(e); relay.relay(e)
    def send(payload):
        text.handle('owner', payload, reply)
    window.on_command = send
    window.apply_text_event(text.hello())
    ticks = []
    heartbeat = QTimer(); heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: ticks.append(1)); heartbeat.start()
    try:
        window._submit_catalog_command('Na sua opinião, como aprender?')
        assert entered.wait(2)
        settle(app, lambda: len(ticks) >= 3)
        assert window.conversation_cancel.isEnabled()
        release.set()
        settle(app, lambda: any(e.get('status') == 'done' for e in events)
               and any('Minha opinião' in e.text for e in window._conversation_snapshot.entries)
               and not window._text_busy)
        assert sink.calls == ['Minha opinião: pratique com exemplos simples.']
        assert any('Minha opinião' in e.text for e in window._conversation_snapshot.entries)
        assert not window._text_busy
    finally:
        release.set(); heartbeat.stop(); text.close(); speech.close()
        close_system(h, admin_controller, window)
