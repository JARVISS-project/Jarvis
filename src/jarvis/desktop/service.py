"""Serviço de domínio para automação segura de desktop."""

import re
import time
import unicodedata
from collections.abc import Callable

from jarvis.desktop.backend import DesktopBackend
from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import (
    DesktopApplication,
    DesktopWindow,
    PreparedDesktopWindow,
    WindowCloseResult,
)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


class DesktopService:
    def __init__(
        self,
        backend: DesktopBackend,
        catalog: DesktopApplicationCatalog,
        kill_switch: DesktopKillSwitch,
        verification_timeout: float = 3.0,
        verification_interval: float = 0.1,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend = backend
        self.catalog = catalog
        self.kill_switch = kill_switch
        self.verification_timeout = verification_timeout
        self.verification_interval = verification_interval
        self.monotonic = monotonic
        self.sleep = sleep

    def list_windows(self) -> list[DesktopWindow]:
        return self.backend.list_windows()

    def list_applications(self) -> list[DesktopApplication]:
        return self.catalog.list_applications()

    def resolve_window(self, query: str) -> DesktopWindow:
        normalized_query = _normalize(query)
        if not normalized_query:
            raise ValueError("Nome da janela não informado.")

        windows = self.list_windows()
        exact = [
            window
            for window in windows
            if _normalize(window.title) == normalized_query
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise ValueError(
                f"Há mais de uma janela com o título exato '{query}'."
            )

        partial = [
            window
            for window in windows
            if normalized_query in _normalize(window.title)
        ]
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise ValueError(f"Janela '{query}' não encontrada.")

        titles = ", ".join(window.title for window in partial[:5])
        raise ValueError(
            f"Janela '{query}' é ambígua. Correspondências: {titles}."
        )

    def focus_window(self, query: str) -> DesktopWindow:
        return self.focus_prepared_window(self.prepare_window(query))

    def prepare_window(self, query: str) -> PreparedDesktopWindow:
        self.kill_switch.require_available()
        window = self.resolve_window(query)
        if window.process_id is None:
            raise RuntimeError("A janela alvo não possui identidade de processo.")
        current = self.backend.inspect_window(window.handle)
        if current is None or not self._same_window_identity(window, current):
            raise RuntimeError("A identidade da janela alvo mudou durante o preparo.")
        return PreparedDesktopWindow(
            public_target=f"janela:{window.title}",
            window=window,
        )

    def refresh_prepared_window(
        self,
        prepared: PreparedDesktopWindow,
    ) -> PreparedDesktopWindow:
        self.validate_prepared_window(prepared)
        rectangle = self.backend.window_capture_rectangle(
            prepared.window.handle
        )
        if rectangle.right <= rectangle.left or rectangle.bottom <= rectangle.top:
            raise RuntimeError("A janela não possui limites utilizáveis.")
        return PreparedDesktopWindow(
            prepared.public_target,
            prepared.window,
            rectangle,
        )

    def validate_prepared_window(
        self,
        prepared: PreparedDesktopWindow,
    ) -> DesktopWindow:
        current = self.backend.inspect_window(prepared.window.handle)
        if current is None:
            raise ValueError("A janela alvo deixou de existir.")
        if not self._same_window_identity(prepared.window, current):
            raise RuntimeError("A identidade da janela alvo mudou.")
        return current

    def focus_prepared_window(
        self,
        prepared: PreparedDesktopWindow,
    ) -> DesktopWindow:
        self.kill_switch.require_available()
        self.validate_prepared_window(prepared)
        self.backend.focus_window(prepared.window.handle)
        return prepared.window

    def resolve_application(self, name: str) -> DesktopApplication:
        return self.catalog.resolve(name)

    def open_application(self, name: str) -> DesktopApplication:
        self.kill_switch.require_available()
        application = self.resolve_application(name)
        self.backend.launch_application(application.executable)
        deadline = self.monotonic() + self.verification_timeout
        while True:
            process_running = self.backend.is_application_running(
                application.process_names
            )
            window_visible = (
                self.backend.has_application_window(
                    application.process_names,
                    application.window_classes,
                )
                if process_running
                else False
            )
            if process_running and window_visible:
                return application
            if self.monotonic() >= deadline:
                detail = (
                    "o processo esperado apareceu, mas nenhuma janela visível "
                    "permitida foi encontrada"
                    if process_running
                    else "o processo esperado não apareceu"
                )
                raise RuntimeError(
                    f'Não foi possível verificar a abertura de "{application.name}": '
                    f"{detail}."
                )
            self.sleep(self.verification_interval)

    def move_pointer_to_window_center(
        self,
        query: str,
    ) -> DesktopWindow:
        return self.move_pointer_to_prepared_window_center(
            self.prepare_window(query)
        )

    def move_pointer_to_prepared_window_center(
        self,
        prepared: PreparedDesktopWindow,
    ) -> DesktopWindow:
        self.kill_switch.require_available()
        self.validate_prepared_window(prepared)
        window = prepared.window
        self.backend.focus_window(window.handle)
        self.kill_switch.require_available()
        self.validate_prepared_window(prepared)
        if self.backend.foreground_window() != window.handle:
            raise RuntimeError("A janela alvo não ficou em primeiro plano.")
        rectangle = self.backend.window_rectangle(window.handle)
        if rectangle.right <= rectangle.left or rectangle.bottom <= rectangle.top:
            raise RuntimeError("A janela não possui limites utilizáveis.")
        x = rectangle.left + (rectangle.right - rectangle.left) // 2
        y = rectangle.top + (rectangle.bottom - rectangle.top) // 2
        self.kill_switch.require_available()
        self.backend.move_pointer(x, y)
        self.kill_switch.require_available()
        if self.backend.pointer_position() != (x, y):
            raise RuntimeError("Não foi possível verificar a posição do ponteiro.")
        return window

    def press_escape(self, query: str) -> DesktopWindow:
        return self.press_escape_prepared(self.prepare_window(query))

    def press_escape_prepared(
        self,
        prepared: PreparedDesktopWindow,
    ) -> DesktopWindow:
        self.kill_switch.require_available()
        self.validate_prepared_window(prepared)
        window = prepared.window
        self.backend.focus_window(window.handle)
        self.kill_switch.require_available()
        self.validate_prepared_window(prepared)
        if self.backend.foreground_window() != window.handle:
            raise RuntimeError("A janela alvo não ficou em primeiro plano.")
        self.kill_switch.require_available()
        escape_pressed = False
        try:
            self.backend.set_escape_pressed(True)
            escape_pressed = True
            self.kill_switch.require_available()
        finally:
            if escape_pressed:
                self.backend.set_escape_pressed(False)
        self.kill_switch.require_available()
        if self.backend.foreground_window() != window.handle:
            raise RuntimeError(
                "Não foi possível verificar a janela após enviar Escape."
            )
        return window

    def close_window(self, query: str) -> WindowCloseResult:
        return self.close_prepared_window(self.prepare_window(query))

    def close_prepared_window(
        self,
        prepared: PreparedDesktopWindow,
    ) -> WindowCloseResult:
        self.kill_switch.require_available()
        self.validate_prepared_window(prepared)
        window = prepared.window
        self.backend.request_window_close(window.handle)
        deadline = self.monotonic() + self.verification_timeout
        while self.backend.window_exists(window.handle):
            self.validate_prepared_window(prepared)
            if self.monotonic() >= deadline:
                return WindowCloseResult(window, closed=False)
            self.sleep(self.verification_interval)
        return WindowCloseResult(window, closed=True)

    @staticmethod
    def _same_window_identity(
        expected: DesktopWindow,
        current: DesktopWindow,
    ) -> bool:
        return (
            current.handle == expected.handle
            and current.process_id == expected.process_id
            and current.class_name == expected.class_name
        )
