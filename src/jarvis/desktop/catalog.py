"""Catálogo explícito de executáveis que o LLM pode solicitar."""

from pathlib import Path
import os
import re
import unicodedata

from jarvis.desktop.models import DesktopApplication


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


class DesktopApplicationCatalog:
    """Allowlist de aplicações; nunca aceita path vindo diretamente do LLM."""

    def __init__(
        self,
        applications: list[DesktopApplication],
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._applications: dict[str, DesktopApplication] = {}
        for application in applications:
            key = _normalize(application.name)
            if not key:
                raise ValueError("Nome de aplicação vazio no catálogo.")
            executable = application.executable.expanduser()
            if not executable.is_absolute():
                raise ValueError(
                    f"Executável de '{application.name}' deve ser absoluto."
                )
            if executable.suffix.casefold() != ".exe":
                raise ValueError(
                    f"Executável de '{application.name}' deve terminar em .exe."
                )
            process_names = application.process_names or (executable.name,)
            for process_name in process_names:
                if (
                    not process_name
                    or Path(process_name).name != process_name
                    or not process_name.casefold().endswith(".exe")
                ):
                    raise ValueError(
                        f"Processo de verificação inválido para "
                        f"'{application.name}'."
                    )
            for window_class in application.window_classes:
                if not re.fullmatch(r"[A-Za-z0-9._-]+", window_class):
                    raise ValueError(
                        f"Classe de janela inválida para '{application.name}'."
                    )
            self._applications[key] = DesktopApplication(
                application.name,
                executable,
                tuple(process_names),
                tuple(application.window_classes),
            )

        self._aliases: dict[str, str] = {}
        for alias, canonical in (aliases or {}).items():
            normalized_alias = _normalize(alias)
            normalized_canonical = _normalize(canonical)
            if normalized_canonical not in self._applications:
                raise ValueError(
                    f"Alias '{alias}' referencia aplicação desconhecida."
                )
            self._aliases[normalized_alias] = normalized_canonical

    def list_applications(self) -> list[DesktopApplication]:
        return sorted(
            self._applications.values(),
            key=lambda item: item.name.casefold(),
        )

    def resolve(self, name: str) -> DesktopApplication:
        key = _normalize(name)
        key = self._aliases.get(key, key)
        application = self._applications.get(key)
        if application is None:
            available = ", ".join(
                item.name for item in self.list_applications()
            ) or "nenhum"
            raise ValueError(
                f"Aplicativo '{name}' não está no catálogo permitido. "
                f"Disponíveis: {available}."
            )
        if not application.executable.is_file():
            raise FileNotFoundError(str(application.executable))
        return application


def windows_default_catalog() -> DesktopApplicationCatalog:
    """Catálogo mínimo, sem shell e sem argumentos arbitrários."""
    windows_directory = Path(
        os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    )
    system32 = windows_directory / "System32"
    applications = [
        DesktopApplication(
            "notepad",
            system32 / "notepad.exe",
            ("notepad.exe",),
            ("Notepad",),
        ),
        DesktopApplication(
            "calculator",
            system32 / "calc.exe",
            ("CalculatorApp.exe", "calc.exe"),
            ("Windows.UI.Core.CoreWindow", "ApplicationFrameWindow"),
        ),
        DesktopApplication(
            "explorer",
            windows_directory / "explorer.exe",
            ("explorer.exe",),
            ("CabinetWClass",),
        ),
    ]
    aliases = {
        "bloco de notas": "notepad",
        "notas": "notepad",
        "calculadora": "calculator",
        "explorador": "explorer",
        "explorador de arquivos": "explorer",
        "file explorer": "explorer",
    }
    return DesktopApplicationCatalog(applications, aliases)
