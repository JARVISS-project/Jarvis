from pathlib import Path
from typing import Any

import pytest

from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import (
    DesktopApplication,
    DesktopWindow,
    WindowRectangle,
)
from jarvis.desktop.service import DesktopService
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.audit import AuditLogger
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.desktop import (
    DesktopCloseWindowTool,
    DesktopFocusWindowTool,
    DesktopMovePointerToWindowTool,
    DesktopOpenApplicationTool,
    DesktopPressEscapeTool,
)
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry


class UnexpectedLLM(LLMClient):
    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        raise AssertionError("routing determinístico não deve chamar o LLM")


class FakeDesktopBackend:
    def __init__(self) -> None:
        self.windows = [
            DesktopWindow(1, "Obsidian", 101),
            DesktopWindow(2, "VS Code", 102),
            DesktopWindow(3, "Calculadora", 103),
            DesktopWindow(4, "Explorador de Arquivos", 104),
            DesktopWindow(5, "Bloco de notas", 105),
        ]
        self.focused: list[int] = []
        self.launched: list[Path] = []
        self.pointer = (0, 0)
        self.foreground: int | None = None
        self.escape_events: list[bool] = []
        self.close_requests: list[int] = []

    def list_windows(self) -> list[DesktopWindow]:
        return list(self.windows)

    def inspect_window(self, handle: int) -> DesktopWindow | None:
        return next(
            (window for window in self.windows if window.handle == handle),
            None,
        )

    def focus_window(self, handle: int) -> None:
        self.focused.append(handle)
        self.foreground = handle

    def launch_application(self, executable: Path) -> None:
        self.launched.append(executable)

    def is_application_running(self, _process_names: tuple[str, ...]) -> bool:
        return True

    def has_application_window(
        self,
        _process_names: tuple[str, ...],
        _window_classes: tuple[str, ...],
    ) -> bool:
        return True

    def window_rectangle(self, _handle: int) -> WindowRectangle:
        return WindowRectangle(0, 0, 100, 80)

    def window_capture_rectangle(self, handle: int) -> WindowRectangle:
        return self.window_rectangle(handle)

    def move_pointer(self, x: int, y: int) -> None:
        self.pointer = (x, y)

    def pointer_position(self) -> tuple[int, int]:
        return self.pointer

    def foreground_window(self) -> int | None:
        return self.foreground

    def set_escape_pressed(self, pressed: bool) -> None:
        self.escape_events.append(pressed)

    def request_window_close(self, handle: int) -> None:
        self.close_requests.append(handle)
        self.windows = [window for window in self.windows if window.handle != handle]

    def window_exists(self, handle: int) -> bool:
        return any(window.handle == handle for window in self.windows)


def build_harness(
    tmp_path: Path,
) -> tuple[Harness, FakeDesktopBackend, DesktopKillSwitch]:
    executable = tmp_path / "calc.exe"
    executable.write_bytes(b"fake")
    backend = FakeDesktopBackend()
    kill_switch = DesktopKillSwitch()
    service = DesktopService(
        backend,
        DesktopApplicationCatalog(
            [
                DesktopApplication(
                    "calculator",
                    executable,
                    ("CalculatorApp.exe", "calc.exe"),
                )
            ],
            {"calculadora": "calculator"},
        ),
        kill_switch,
    )
    registry = ToolRegistry()
    registry.register(DesktopFocusWindowTool(service))
    registry.register(DesktopOpenApplicationTool(service))
    registry.register(DesktopMovePointerToWindowTool(service))
    registry.register(DesktopPressEscapeTool(service))
    registry.register(DesktopCloseWindowTool(service))

    harness = Harness(
        llm=UnexpectedLLM(),
        tool_registry=registry,
        tool_executor=ToolExecutor(
            SafetyManager(),
            AuditLogger(tmp_path / "audit.log"),
        ),
        memory=ConversationMemory(),
    )
    return harness, backend, kill_switch


@pytest.mark.parametrize(
    ("message", "handle"),
    [
        ("Foca a janela do Obsidian.", 1),
        ("Vai pro Obsidian.", 1),
        ("Coloca o VS Code em foco.", 2),
        ("Traz a calculadora pra frente.", 3),
    ],
)
def test_natural_window_request_executes_without_llm(
    tmp_path: Path,
    message: str,
    handle: int,
) -> None:
    harness, backend, _kill_switch = build_harness(tmp_path)

    result = harness.processar_mensagem(message)

    assert "Autoriza" in result
    assert backend.focused == []


def test_application_open_requires_verified_visible_window(tmp_path: Path) -> None:
    harness, backend, _kill_switch = build_harness(tmp_path)

    # Use the canonical name so schema validation passes
    result = harness.processar_mensagem("Abra o calculator.")

    assert "Autoriza" in result
    assert len(backend.launched) == 0


def test_kill_switch_block_is_returned_without_llm_fallback(tmp_path: Path) -> None:
    harness, backend, kill_switch = build_harness(tmp_path)
    kill_switch.trip("Ctrl+Alt+K acionado")

    result = harness.processar_mensagem("Vai pro Obsidian.")

    assert "bloqueado" in result
    assert "kill switch" in result.casefold()
    assert "Ctrl+Alt+K" in result
    assert backend.focused == []


def test_recent_window_reference_is_resolved_without_llm(tmp_path: Path) -> None:
    harness, backend, _kill_switch = build_harness(tmp_path)
    harness.recent_context.set("last_desktop_window", "Obsidian")

    result = harness.processar_mensagem("Traz ela de volta.")

    assert "Autoriza" in result
    assert backend.focused == []


def test_window_reference_without_context_requests_explicit_title(
    tmp_path: Path,
) -> None:
    harness, backend, _kill_switch = build_harness(tmp_path)

    result = harness.processar_mensagem("Foca nela.")

    assert "janela recente inequívoca" in result
    assert backend.focused == []


def test_recent_window_reference_rechecks_ambiguity(tmp_path: Path) -> None:
    harness, backend, _kill_switch = build_harness(tmp_path)
    harness.recent_context.set("last_desktop_window", "Obsidian")
    backend.windows.append(DesktopWindow(4, "Obsidian", 104))

    result = harness.processar_mensagem("Volta pra ela.")

    assert "mais de uma janela" in result
    assert backend.focused == []


def test_voice_transcription_variants_route_without_llm(tmp_path: Path) -> None:
    harness, backend, _kill_switch = build_harness(tmp_path)

    pointer = harness.processar_mensagem(
        "Movam o mouse para o centro da janela do explorador de arquivos."
    )
    harness.recent_context.set("last_desktop_window", "Explorador de Arquivos")
    context = harness.current_confirmation_context()
    harness.processar_mensagem("deixa pra l\u00e1", confirmation_session_id=context[0], confirmation_challenge_id=context[1])
    repeated = harness.processar_mensagem("Mova novamente.")
    context = harness.current_confirmation_context()
    harness.processar_mensagem("deixa pra l\u00e1", confirmation_session_id=context[0], confirmation_challenge_id=context[1])
    escape = harness.processar_mensagem(
        "Pressiona o Escape e da janela Bloco de notas."
    )

    assert "Autoriza" in pointer
    assert "Autoriza" in repeated
    assert "Autoriza" in escape
    assert backend.pointer == (0, 0)
    assert backend.escape_events == []


def test_escape_without_target_requests_window_without_llm(tmp_path: Path) -> None:
    harness, backend, _kill_switch = build_harness(tmp_path)

    response = harness.processar_mensagem("E pressione Escape.")

    assert "em qual janela" in response
    assert "não vou inferir" in response
    assert backend.escape_events == []


def test_close_notepad_routes_without_llm_and_verifies_absence(
    tmp_path: Path,
) -> None:
    harness, backend, _kill_switch = build_harness(tmp_path)

    response = harness.processar_mensagem("Feche o Bloco de notas.")

    assert "Autoriza" in response
    assert backend.close_requests == []
    assert backend.window_exists(5)
