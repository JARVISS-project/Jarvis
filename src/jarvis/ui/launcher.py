"""Entrada lazy da interface, mantendo PySide6 fora do Core."""

from pathlib import Path

from jarvis.application.state import AgentStateBus
from jarvis.application.state_ipc import LocalStateClient, StateEndpoint
from jarvis.application.telemetry import TelemetryBus
from jarvis.ui.control_center_model import ControlCenterBus


_UI_INSTALL_HINT = 'python -m pip install -e ".[ui]"'


def run_ambient_ui_demo(
    state_bus: AgentStateBus | None = None,
    telemetry_bus: TelemetryBus | None = None,
    control_center_bus: ControlCenterBus | None = None,
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

    return run_qt_ambient_ui(
        state_bus=state_bus or AgentStateBus(),
        debug_mode=True,
        telemetry_bus=telemetry_bus or TelemetryBus(),
        control_center_bus=control_center_bus or ControlCenterBus(),
    )


def run_ambient_ui_live(endpoint_path: Path) -> int:
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

    endpoint = StateEndpoint.read(endpoint_path)
    state_bus = AgentStateBus()
    telemetry_bus = TelemetryBus()
    control_center_bus = ControlCenterBus()
    client = LocalStateClient(
        state_bus,
        endpoint,
        telemetry_bus=telemetry_bus,
    )
    client.start()
    try:
        return run_qt_ambient_ui(
            state_bus=state_bus,
            debug_mode=False,
            telemetry_bus=telemetry_bus,
            control_center_bus=control_center_bus,
        )
    finally:
        client.close()
