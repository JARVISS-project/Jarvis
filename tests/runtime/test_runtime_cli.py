import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.runtime import cli


def test_background_does_not_enable_voice_remote_or_ui() -> None:
    args = cli.argument_parser().parse_args(["--background"])

    assert args.background is True
    assert args.voice is False
    assert args.remote is False
    assert args.ui is False


@pytest.mark.parametrize(
    ("arguments", "voice", "remote", "ui"),
    [
        (["--voice"], True, False, False),
        (["--remote"], False, True, False),
        (["--ui"], False, False, True),
        (["--voice", "--remote", "--ui"], True, True, True),
    ],
)
def test_runtime_flags_are_independent(arguments, voice, remote, ui) -> None:
    args = cli.argument_parser().parse_args(arguments)
    assert (args.voice, args.remote, args.ui) == (voice, remote, ui)


def test_background_launcher_uses_pythonw_and_preserves_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    python = tmp_path / "python.exe"
    pythonw = tmp_path / "pythonw.exe"
    pythonw.touch()
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.sys, "executable", str(python))
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    cli._launch_background_child(["--background", "--remote"])

    command, kwargs = calls[0]
    assert command[0] == str(pythonw)
    assert "--background" not in command
    assert "--background-child" in command
    assert "--remote" in command
    assert kwargs["close_fds"] is True


def test_startup_rejects_invalid_remote_configuration_before_install() -> None:
    args = cli.argument_parser().parse_args(
        [
            "--startup",
            "install",
            "--startup-remote",
            "--remote-transport",
            "tcp",
            "--remote-websocket-origin",
            "http://127.0.0.1:5173",
        ]
    )

    with pytest.raises(SystemExit, match="websocket-origin"):
        cli._validate_startup_args(args)


def test_google_commands_are_local_and_do_not_start_runtime(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
    from jarvis.integrations.google import factory

    calls: list[str] = []

    class FakeManager:
        def status(self):
            calls.append("status")
            return ConnectionStatus(ConnectionState.CONNECTED, ("openid",))

    monkeypatch.setattr(factory, "build_google_auth_manager", lambda: FakeManager())
    monkeypatch.setattr(
        cli,
        "_run_console_runtime",
        lambda _config: (_ for _ in ()).throw(AssertionError("runtime started")),
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--google", "status"])

    assert exit_info.value.code == 0
    assert calls == ["status"]
    assert capsys.readouterr().out.strip() == "Google conectado."


def test_google_connect_cli_never_accepts_tokens_as_arguments() -> None:
    parser = cli.argument_parser()
    option_names = {
        option
        for action in parser._actions
        for option in action.option_strings
    }

    assert "--google-access-token" not in option_names
    assert "--google-refresh-token" not in option_names
    assert "--google-client-secret" not in option_names


def test_google_calendar_command_uses_minimal_read_scopes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from jarvis.integrations.google import factory
    from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
    from jarvis.integrations.google.scopes import CALENDAR_MINIMAL_READ_SCOPES

    calls: list[str] = []

    class FakeManager:
        def status(self):
            calls.append("status")
            return ConnectionStatus(ConnectionState.CONNECTED, CALENDAR_MINIMAL_READ_SCOPES)

    monkeypatch.setattr(
        factory,
        "build_google_calendar_minimal_read_auth",
        lambda: FakeManager(),
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--google-calendar", "status"])

    assert exit_info.value.code == 0
    assert calls == ["status"]
    assert capsys.readouterr().out.strip() == "Google Calendar conectado."


def test_google_calendar_write_command_uses_separate_manager(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from jarvis.integrations.google import factory
    from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
    from jarvis.integrations.google.scopes import CALENDAR_LIMITED_WRITE_SCOPES

    calls: list[str] = []

    class FakeManager:
        def status(self):
            calls.append("write-status")
            return ConnectionStatus(ConnectionState.CONNECTED, CALENDAR_LIMITED_WRITE_SCOPES)

    monkeypatch.setattr(
        factory,
        "build_google_calendar_limited_write_auth",
        lambda: FakeManager(),
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--google-calendar-write", "status"])

    assert exit_info.value.code == 0
    assert calls == ["write-status"]
    assert capsys.readouterr().out.strip() == "Google Calendar Write conectado."


def test_runtime_stop_cancels_starting_instance_immediately(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    current = SimpleNamespace(
        state=cli.RuntimeState.STARTING.value,
        pid=4321,
        instance_id="instance-1",
    )

    class Store:
        requested: list[str] = []
        removed: list[str] = []
        cleared = 0

        def __init__(self, _path: Path) -> None:
            pass

        def read(self):
            return current

        def request_stop(self, instance_id: str) -> None:
            self.requested.append(instance_id)

        def remove_status(self, instance_id: str) -> None:
            self.removed.append(instance_id)

        def clear_stop_request(self) -> None:
            self.cleared += 1

    store = Store(tmp_path / "status.json")
    monkeypatch.setattr(cli, "RuntimeHealthStore", lambda _path: store)
    monkeypatch.setattr(cli, "_runtime_paths", lambda: (
        tmp_path / "instance.json",
        tmp_path / "status.json",
        tmp_path / "runtime.log",
    ))
    monkeypatch.setattr(cli, "process_is_running", lambda _pid: True)
    terminated: list[int] = []
    monkeypatch.setattr(
        cli,
        "_terminate_starting_runtime",
        lambda pid: terminated.append(pid) or True,
    )
    removed_metadata: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        cli,
        "_remove_instance_metadata",
        lambda path, instance_id: removed_metadata.append((path, instance_id)),
    )

    result = cli._handle_runtime_command(
        cli.argument_parser().parse_args(["--runtime-stop"])
    )

    assert result == 0
    assert store.requested == ["instance-1"]
    assert store.removed == ["instance-1"]
    assert store.cleared == 1
    assert terminated == [4321]
    assert removed_metadata == [(tmp_path / "instance.json", "instance-1")]
    assert "cancelado" in capsys.readouterr().out


def test_runtime_stop_keeps_running_instance_cooperative(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    current = SimpleNamespace(
        state=cli.RuntimeState.RUNNING.value,
        pid=4321,
        instance_id="instance-2",
    )

    class Store:
        requested: list[str] = []

        def __init__(self, _path: Path) -> None:
            pass

        def read(self):
            return current

        def request_stop(self, instance_id: str) -> None:
            self.requested.append(instance_id)

    store = Store(tmp_path / "status.json")
    monkeypatch.setattr(cli, "RuntimeHealthStore", lambda _path: store)
    monkeypatch.setattr(cli, "_runtime_paths", lambda: (
        tmp_path / "instance.json",
        tmp_path / "status.json",
        tmp_path / "runtime.log",
    ))
    monkeypatch.setattr(cli, "process_is_running", lambda _pid: True)
    monkeypatch.setattr(
        cli,
        "_terminate_starting_runtime",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("RUNNING must not be force-stopped")
        ),
    )

    result = cli._handle_runtime_command(
        cli.argument_parser().parse_args(["--runtime-stop"])
    )

    assert result == 0
    assert store.requested == ["instance-2"]
    assert "Solicitação de parada enviada" in capsys.readouterr().out
