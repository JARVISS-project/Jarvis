from pathlib import Path
import time

from jarvis.application.state import AgentState, AgentStateBus
from jarvis.application.state_ipc import LocalStateClient, LocalStateServer
from jarvis.application.telemetry import TelemetryBus
from jarvis.application.tool_execution_state import AgentToolExecutionObserver
from jarvis.application.voice_bridge import VoiceStateBridge
from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import DesktopWindow, WindowRectangle
from jarvis.desktop.service import DesktopService
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.desktop import (
    DesktopMovePointerToWindowTool,
    DesktopPressEscapeTool,
)
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.pipeline import VoicePipeline


class PointerBackend:
    def __init__(self, *, verified: bool = True) -> None:
        self.window = DesktopWindow(7, "Obsidian", 70, "TestWindow")
        self.pointer = (0, 0)
        self.verified = verified
        self.foreground: int | None = None
        self.escape_events: list[bool] = []

    def list_windows(self) -> list[DesktopWindow]:
        return [self.window]

    def inspect_window(self, handle: int) -> DesktopWindow | None:
        return self.window if handle == self.window.handle else None

    def focus_window(self, handle: int) -> None:
        self.foreground = handle

    def launch_application(self, _executable: Path) -> None:
        raise AssertionError("não deveria abrir aplicativo")

    def is_application_running(self, _process_names: tuple[str, ...]) -> bool:
        return False

    def has_application_window(
        self,
        _process_names: tuple[str, ...],
        _window_classes: tuple[str, ...],
    ) -> bool:
        return False

    def window_rectangle(self, _handle: int) -> WindowRectangle:
        return WindowRectangle(0, 0, 100, 80)

    def window_capture_rectangle(self, handle: int) -> WindowRectangle:
        return self.window_rectangle(handle)

    def move_pointer(self, x: int, y: int) -> None:
        self.pointer = (x, y)

    def pointer_position(self) -> tuple[int, int]:
        return self.pointer if self.verified else (0, 0)

    def foreground_window(self) -> int | None:
        return self.foreground

    def set_escape_pressed(self, pressed: bool) -> None:
        self.escape_events.append(pressed)


class PointerLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("routing determinístico não deve chamar o LLM")


def _wait_until(predicate, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Condição não ocorreu dentro do timeout.")


def build_runtime(
    tmp_path: Path,
    *,
    verified: bool,
) -> tuple[
    Harness,
    PointerBackend,
    PointerLLM,
    AgentStateBus,
    TelemetryBus,
    Path,
]:
    backend = PointerBackend(verified=verified)
    service = DesktopService(
        backend,
        DesktopApplicationCatalog([]),
        DesktopKillSwitch(),
    )
    registry = ToolRegistry()
    registry.register(DesktopMovePointerToWindowTool(service))
    registry.register(DesktopPressEscapeTool(service))
    state_bus = AgentStateBus(AgentState.PROCESSING)
    telemetry_bus = TelemetryBus()
    audit_path = tmp_path / "audit.log"
    llm = PointerLLM()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=ToolExecutor(
            SafetyManager(),
            AuditLogger(audit_path, telemetry_bus.record_audit),
            capability_policy=CapabilityPolicy(
                {Capability.DESKTOP_CONTROL: True}
            ),
            observer=AgentToolExecutionObserver(state_bus),
        ),
        memory=ConversationMemory(),
        telemetry_bus=telemetry_bus,
    )
    return harness, backend, llm, state_bus, telemetry_bus, audit_path


def test_desktop_input_runtime_publishes_ui_state_audits_and_latency(
    tmp_path: Path,
) -> None:
    harness, backend, llm, runtime_bus, runtime_telemetry, audit_path = build_runtime(
        tmp_path,
        verified=True,
    )
    runtime_states: list[AgentState] = []
    runtime_bus.subscribe(
        lambda snapshot: runtime_states.append(snapshot.state),
        replay_current=False,
    )
    ui_bus = AgentStateBus()
    ui_telemetry = TelemetryBus()
    ui_snapshots = []
    ui_bus.subscribe(
        ui_snapshots.append,
        replay_current=False,
    )
    server = LocalStateServer(
        runtime_bus,
        endpoint_path=tmp_path / "ambient-ui.json",
        telemetry_bus=runtime_telemetry,
    )
    endpoint = server.start()
    client = LocalStateClient(
        ui_bus,
        endpoint,
        reconnect_seconds=0.01,
        telemetry_bus=ui_telemetry,
    )
    client.start()
    try:
        runtime_bus.publish(AgentState.PROCESSING, source="voice_pipeline")
        _wait_until(lambda: ui_bus.snapshot.state is AgentState.PROCESSING)

        pipeline = VoicePipeline(
            listener=object(),  # type: ignore[arg-type]
            recorder=object(),  # type: ignore[arg-type]
            speech_to_text=object(),  # type: ignore[arg-type]
            harness=harness,
            resource_manager=ResourceManager(),
            text_to_speech=None,
            output=lambda _message: None,
            state_callback=VoiceStateBridge(runtime_bus),
        )
        processed, spoke = pipeline._process_text(  # noqa: SLF001
            "Mova o mouse para o centro da janela do Obsidian."
        )
        _wait_until(lambda: ui_bus.snapshot.sequence >= 3)
        _wait_until(lambda: ui_telemetry.snapshot.has_completed_turn)

        assert processed
        assert not spoke
        assert "posição verificada" in pipeline._last_response  # noqa: SLF001
        assert backend.pointer == (50, 40)
        assert llm.calls == 0
        assert runtime_states[-2:] == [
            AgentState.EXECUTING,
            AgentState.PROCESSING,
        ]
        assert AgentState.EXECUTING in {
            snapshot.state for snapshot in ui_snapshots
        }
        assert ui_bus.snapshot.state is AgentState.PROCESSING
        assert all(snapshot.detail is None for snapshot in ui_snapshots)
        assert harness.last_latency["tool_calls"] == 1
        assert harness.last_latency["llm_calls"] == 0
        assert ui_telemetry.snapshot.llm_calls == 0
        assert ui_telemetry.snapshot.tool_calls == 1
        assert ui_telemetry.snapshot.audit_events == 1
        assert ui_telemetry.snapshot.audit_write_ok is True
        audit = audit_path.read_text(encoding="utf-8")
        assert "tool=desktop_move_pointer_to_window" in audit
        assert "authorized=True status=success" in audit
    finally:
        client.close()
        server.close()


def test_desktop_input_runtime_failure_publishes_error_and_stops_llm(
    tmp_path: Path,
) -> None:
    harness, _backend, llm, state_bus, _telemetry, audit_path = build_runtime(
        tmp_path,
        verified=False,
    )
    seen: list[AgentState] = []
    state_bus.subscribe(
        lambda snapshot: seen.append(snapshot.state),
        replay_current=False,
    )

    response = harness.processar_mensagem(
        "Mova o mouse para o centro da janela do Obsidian."
    )

    assert "verificar a posição" in response
    assert llm.calls == 0
    assert seen == [AgentState.EXECUTING, AgentState.ERROR]
    assert state_bus.snapshot.state is AgentState.ERROR
    audit = audit_path.read_text(encoding="utf-8")
    assert "authorized=True status=error" in audit


def test_escape_uses_same_runtime_authorization_and_audit_path(
    tmp_path: Path,
) -> None:
    harness, backend, llm, state_bus, _telemetry, audit_path = build_runtime(
        tmp_path,
        verified=True,
    )
    seen: list[AgentState] = []
    state_bus.subscribe(
        lambda snapshot: seen.append(snapshot.state),
        replay_current=False,
    )

    response = harness.processar_mensagem(
        "Pressione Escape na janela do Obsidian.",
        response_mode="voice",
    )

    assert "Escape enviado" in response
    assert backend.escape_events == [True, False]
    assert llm.calls == 0
    assert seen == [AgentState.EXECUTING, AgentState.PROCESSING]
    audit = audit_path.read_text(encoding="utf-8")
    assert "tool=desktop_press_escape" in audit
    assert "authorized=True status=success" in audit
