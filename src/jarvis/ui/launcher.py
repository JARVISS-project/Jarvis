"""Entrada lazy da interface, mantendo PySide6 fora do Core."""

from pathlib import Path

from jarvis.application.audio_activity import AudioActivityBus
from jarvis.application.conversation_log import ConversationLogBus
from jarvis.application.state import AgentStateBus
from jarvis.application.state_ipc import LocalStateClient, StateEndpoint
from jarvis.application.telemetry import TelemetryBus
from jarvis.application.text_interaction import TextEventBus
from jarvis.config import JarvisConfig
from jarvis.ui.control_center_model import ControlCenterBus


_UI_INSTALL_HINT = 'python -m pip install -e ".[ui]"'


def run_ambient_ui_demo(
    state_bus: AgentStateBus | None = None,
    telemetry_bus: TelemetryBus | None = None,
    control_center_bus: ControlCenterBus | None = None,
    audio_activity_bus: AudioActivityBus | None = None,
    conversation_log_bus: ConversationLogBus | None = None,
    open_control_center: bool = False,
) -> int:
    """Executa a Ambient UI em modo de simulação, sem voz ou Ollama."""

    try:
        from jarvis.ui.qt_app import run_qt_ambient_ui
    except ImportError as error:
        if error.name and error.name.startswith("PySide6"):
            raise RuntimeError(
                "PySide6 não está instalado. Instale a interface com: "
                f"{_UI_INSTALL_HINT}"
            ) from error
        raise

    config = JarvisConfig()
    return run_qt_ambient_ui(
        state_bus=state_bus or AgentStateBus(),
        debug_mode=True,
        telemetry_bus=telemetry_bus or TelemetryBus(),
        control_center_bus=control_center_bus or ControlCenterBus(),
        audio_activity_bus=audio_activity_bus or AudioActivityBus(),
        conversation_log_bus=conversation_log_bus or ConversationLogBus(),
        open_control_center=open_control_center,
        user_display_name=config.user_display_name,
        agent_name=config.agent_name,
        identity_saver=config.save_identity_settings,
        current_microphone=config.microphone_device_name,
        microphone_saver=config.save_microphone_settings,
    )


def run_ambient_ui_live(endpoint_path: Path, *, open_control_center: bool = False) -> int:
    """Conecta a Ambient UI a um Voice Runtime já em execução."""

    try:
        from jarvis.ui.qt_app import run_qt_ambient_ui
    except ImportError as error:
        if error.name and error.name.startswith("PySide6"):
            raise RuntimeError(
                "PySide6 não está instalado. Instale a interface com: "
                f"{_UI_INSTALL_HINT}"
            ) from error
        raise

    config = JarvisConfig()
    endpoint = StateEndpoint.read(endpoint_path)
    state_bus = AgentStateBus()
    telemetry_bus = TelemetryBus()
    control_center_bus = ControlCenterBus()
    audio_activity_bus = AudioActivityBus()
    conversation_log_bus = ConversationLogBus()
    text_bus = TextEventBus()
    client = LocalStateClient(
        state_bus,
        endpoint,
        telemetry_bus=telemetry_bus,
        audio_activity_bus=audio_activity_bus,
        conversation_log_bus=conversation_log_bus,
        text_bus=text_bus,
        endpoint_path=endpoint_path,
    )
    client.start()
    try:
        return run_qt_ambient_ui(
            state_bus=state_bus,
            debug_mode=False,
            open_control_center=open_control_center,
            telemetry_bus=telemetry_bus,
            control_center_bus=control_center_bus,
            audio_activity_bus=audio_activity_bus,
            conversation_log_bus=conversation_log_bus,
            user_display_name=config.user_display_name,
            agent_name=config.agent_name,
            identity_saver=config.save_identity_settings,
            current_microphone=config.microphone_device_name,
            microphone_saver=config.save_microphone_settings,
            on_command=client.send_command,
            text_bus=text_bus,
        )
    finally:
        client.close()
