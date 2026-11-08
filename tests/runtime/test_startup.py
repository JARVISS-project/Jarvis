from pathlib import Path

from jarvis.runtime.startup import StartupOptions, WindowsStartupManager


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, command: str) -> None:
        self.values[name] = command

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def remove(self, name: str) -> None:
        self.values.pop(name, None)


def test_startup_install_preserves_selected_capabilities_without_secrets(
    tmp_path: Path,
) -> None:
    registry = FakeRegistry()
    manager = WindowsStartupManager(registry, Path("C:/Python/pythonw.exe"))
    options = StartupOptions(
        voice=True,
        remote=True,
        ui=False,
        remote_transport="websocket",
        remote_websocket_origins=("http://127.0.0.1:5173",),
    )

    command = manager.install(options, tmp_path)

    assert "--background-child" in command
    assert "--voice" in command
    assert "--remote" in command
    assert "--ui" not in command
    assert "--remote-transport websocket" in command
    assert "token" not in command.casefold()
    assert manager.status() == command


def test_startup_remove_is_idempotent(tmp_path: Path) -> None:
    registry = FakeRegistry()
    manager = WindowsStartupManager(registry, Path("pythonw.exe"))
    manager.install(StartupOptions(), tmp_path)

    manager.remove()
    manager.remove()

    assert manager.status() is None
