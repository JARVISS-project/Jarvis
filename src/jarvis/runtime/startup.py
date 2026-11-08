"""Autostart explícito na sessão interativa do usuário Windows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Protocol


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "JARVIS"


class StartupRegistry(Protocol):
    def set(self, name: str, command: str) -> None: ...
    def get(self, name: str) -> str | None: ...
    def remove(self, name: str) -> None: ...


class WindowsRunRegistry:
    def set(self, name: str, command: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)

    def get(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, name)
                return str(value)
        except FileNotFoundError:
            return None

    def remove(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                RUN_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return


@dataclass(frozen=True, slots=True)
class StartupOptions:
    voice: bool = False
    remote: bool = False
    ui: bool = False
    remote_transport: str = "tcp"
    remote_host: str = "127.0.0.1"
    remote_port: int = 8766
    remote_websocket_origins: tuple[str, ...] = ()
    remote_allow_lan: bool = False
    remote_cert: Path | None = None
    remote_key: Path | None = None


class WindowsStartupManager:
    def __init__(
        self,
        registry: StartupRegistry | None = None,
        executable: Path | None = None,
    ) -> None:
        self.registry = registry or WindowsRunRegistry()
        self.executable = executable or self._pythonw_executable()

    def install(self, options: StartupOptions, runtime_root: Path) -> str:
        command = self.build_command(options, runtime_root)
        command_line = subprocess.list2cmdline(command)
        self.registry.set(VALUE_NAME, command_line)
        return command_line

    def remove(self) -> None:
        self.registry.remove(VALUE_NAME)

    def status(self) -> str | None:
        return self.registry.get(VALUE_NAME)

    def build_command(
        self,
        options: StartupOptions,
        runtime_root: Path,
    ) -> list[str]:
        command = [
            str(self.executable),
            "-m",
            "jarvis",
            "--background-child",
            "--runtime-root",
            str(runtime_root.resolve()),
        ]
        if options.voice:
            command.append("--voice")
        if options.ui:
            command.append("--ui")
        if options.remote:
            command.extend(
                [
                    "--remote",
                    "--remote-transport",
                    options.remote_transport,
                    "--remote-host",
                    options.remote_host,
                    "--remote-port",
                    str(options.remote_port),
                ]
            )
            for origin in options.remote_websocket_origins:
                command.extend(["--remote-websocket-origin", origin])
            if options.remote_allow_lan:
                command.append("--remote-allow-lan")
            if options.remote_cert is not None:
                command.extend(["--remote-cert", str(options.remote_cert)])
            if options.remote_key is not None:
                command.extend(["--remote-key", str(options.remote_key)])
        return command

    @staticmethod
    def _pythonw_executable() -> Path:
        executable = Path(sys.executable)
        candidate = executable.with_name("pythonw.exe")
        return candidate if candidate.exists() else executable
