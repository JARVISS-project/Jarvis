import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from jarvis.application.audio_activity import AudioActivityBus
from jarvis.application.conversation_log import ConversationLogBus
from jarvis.application.state import AgentState, AgentStateBus
from jarvis.application.telemetry import TelemetryBus
from jarvis.ui.control_center_model import ControlCenterBus
from jarvis.ui.qt_app import AmbientHUDWidget, AmbientWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_ptt_button_emits_only_capture_commands_and_release_on_hide():
    app = _app()
    commands = []
    window = AmbientWindow(AgentStateBus(), debug_mode=False, on_command=commands.append)
    control = window.control_center_window
    try:
        control.show()
        app.processEvents()
        control.ptt_button.pressed.emit()
        control.ptt_button.released.emit()
        control.hide()
        assert commands == [
            {"command": "ptt_start"}, {"command": "ptt_stop"}, {"command": "ptt_stop"},
        ]
    finally:
        window.hide()


def test_microphone_picker_warns_probes_selected_index_and_saves_name(monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QPushButton
    from jarvis.audio.devices import AudioDevice
    from jarvis.ui import qt_app

    _app()
    monkeypatch.setattr(qt_app, "list_input_devices", lambda: [AudioDevice(4, "USB Mic")])
    probed, saved, errors = [], [], []
    monkeypatch.setattr(qt_app, "probe_microphone", lambda index: probed.append(index) or True)
    window = AmbientWindow(AgentStateBus(), debug_mode=False,
                           current_microphone="Missing", microphone_saver=saved.append)

    def inspect_dialog():
        dialog = window.control_center_window.findChild(QDialog)
        try:
            combo = dialog.findChild(QComboBox)
            assert combo.currentData() is None
            assert any("indisponível" in label.text() for label in dialog.findChildren(QLabel))
            combo.setCurrentIndex(1)
            next(b for b in dialog.findChildren(QPushButton) if b.text() == "Testar").click()
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Save).click()
        except BaseException as error:
            errors.append(error)
            dialog.reject()

    QTimer.singleShot(0, inspect_dialog)
    window.control_center_window.open_identity_settings()
    window.hide()
    assert not errors
    assert probed == [4]
    assert saved == ["USB Mic"]


def test_listening_orb_smooths_real_audio_level_inside_widget() -> None:
    _app()
    orb = AmbientHUDWidget()
    orb.set_state(AgentState.LISTENING)
    orb.set_audio_activity(0.8)

    orb._advance_animation()

    assert 0.0 < orb._audio_level_display < 0.8


def test_ambient_orb_opens_separate_control_center() -> None:
    app = _app()
    window = AmbientWindow(
        AgentStateBus(),
        debug_mode=False,
        telemetry_bus=TelemetryBus(),
        control_center_bus=ControlCenterBus(),
        audio_activity_bus=AudioActivityBus(),
    )
    try:
        assert window.control_center_window.isVisible() is False

        window.hud.clicked.emit()
        app.processEvents()

        assert window.control_center_window.isVisible() is True
        assert window.panel.isVisible() is False
    finally:
        window.control_center_window.hide()
        window.hide()


def test_control_center_shows_user_and_agent_identity() -> None:
    _app()
    window = AmbientWindow(
        AgentStateBus(),
        debug_mode=False,
        telemetry_bus=TelemetryBus(),
        control_center_bus=ControlCenterBus(),
        audio_activity_bus=AudioActivityBus(),
        user_display_name="Pessoa",
        agent_name="Atlas",
    )
    try:
        control_center = window.control_center_window

        assert control_center.user_label.text() == "Pessoa"
        assert control_center.agent_identity_label.text() == "Agente · Atlas"
        assert control_center.agent_title_label.text() == "Atlas"
    finally:
        window.control_center_window.hide()
        window.hide()


def test_control_center_renders_recent_conversation() -> None:
    _app()
    conversation_log = ConversationLogBus()
    window = AmbientWindow(
        AgentStateBus(),
        debug_mode=False,
        telemetry_bus=TelemetryBus(),
        control_center_bus=ControlCenterBus(),
        audio_activity_bus=AudioActivityBus(),
        conversation_log_bus=conversation_log,
        user_display_name="Pessoa",
        agent_name="Jarvis",
    )
    try:
        conversation_log.add_user("Que horas são?")
        conversation_log.add_assistant("Agora são 14:32.")
        _app().processEvents()

        rendered = window.control_center_window.conversation_body.text()
        assert "Pessoa" in rendered
        assert "Que horas são?" in rendered
        assert "Jarvis" in rendered
        assert "Agora são 14:32." in rendered
    finally:
        window.control_center_window.hide()
        window.hide()


def test_conversation_tab_renders_ephemeral_session_without_new_authority() -> None:
    app = _app()
    conversation_log = ConversationLogBus()
    window = AmbientWindow(
        AgentStateBus(),
        debug_mode=False,
        telemetry_bus=TelemetryBus(),
        control_center_bus=ControlCenterBus(),
        audio_activity_bus=AudioActivityBus(),
        conversation_log_bus=conversation_log,
        user_display_name="Pessoa",
        agent_name="Jarvis",
    )
    try:
        conversation_log.add_user("Qual é o status da sprint?")
        conversation_log.add_assistant("A sprint de Voice continua ativa.")
        app.processEvents()

        control_center = window.control_center_window
        control_center.nav_buttons["Conversa"].click()
        app.processEvents()

        assert control_center.page_stack.currentWidget() is control_center.conversation_page
        assert control_center.nav_buttons["Conversa"].objectName() == "navActive"
        assert control_center.conversation_message_count.text() == "2 mensagens"
        assert "Somente memória" in control_center.conversation_details.text()
        assert "Desativada" in control_center.conversation_details.text()

        rendered = " ".join(
            label.text()
            for label in control_center.conversation_messages_container.findChildren(
                QLabel
            )
        )
        assert "Pessoa" in rendered
        assert "Qual é o status da sprint?" in rendered
        assert "Jarvis" in rendered
        assert "A sprint de Voice continua ativa." in rendered

        assert control_center.conversation_input.isReadOnly() is True
        assert control_center.conversation_send.isEnabled() is False
        assert control_center.conversation_export_button.isEnabled() is False
        assert control_center.conversation_clear_button.isEnabled() is False
    finally:
        window.control_center_window.hide()
        window.hide()


def test_conversation_chat_uses_larger_readable_font() -> None:
    app = _app()
    conversation_log = ConversationLogBus()
    window = AmbientWindow(
        AgentStateBus(),
        debug_mode=False,
        telemetry_bus=TelemetryBus(),
        control_center_bus=ControlCenterBus(),
        audio_activity_bus=AudioActivityBus(),
        conversation_log_bus=conversation_log,
        user_display_name="Pessoa",
        agent_name="Jarvis",
    )
    try:
        conversation_log.add_user("Teste de legibilidade do chat.")
        app.processEvents()

        control_center = window.control_center_window
        labels = control_center.conversation_messages_container.findChildren(QLabel)
        message_labels = [
            label for label in labels
            if label.objectName() == "conversationMessage"
        ]

        assert len(message_labels) == 1
        stylesheet = control_center.styleSheet()
        assert "QLabel#conversationMessage" in stylesheet
        assert "font-size: 13px;" in stylesheet
        assert control_center.conversation_body.objectName() == "conversationRecent"
    finally:
        window.control_center_window.hide()
        window.hide()
