"""Serviço de domínio para automação segura de desktop."""

import re
import time
import unicodedata
from collections.abc import Callable

from jarvis.desktop.backend import DesktopBackend
from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.models import DesktopApplication, DesktopWindow


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
        self.kill_switch.require_available()
        window = self.resolve_window(query)
        self.backend.focus_window(window.handle)
        return window

    def resolve_application(self, name: str) -> DesktopApplication:
        return self.catalog.resolve(name)

    def open_application(self, name: str) -> DesktopApplication:
        self.kill_switch.require_available()
        application = self.resolve_application(name)
        self.backend.launch_application(application.executable)
        deadline = self.monotonic() + self.verification_timeout
        while True:
            if self.backend.is_application_running(application.process_names):
                return application
            if self.monotonic() >= deadline:
                raise RuntimeError(
                    f'Não foi possível verificar a abertura de "{application.name}".'
                )
            self.sleep(self.verification_interval)
