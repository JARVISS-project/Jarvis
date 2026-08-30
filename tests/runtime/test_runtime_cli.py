import subprocess
from pathlib import Path

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


def test_google_calendar_command_uses_calendar_scopes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from jarvis.integrations.google import factory
    from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
    from jarvis.integrations.google.scopes import CALENDAR_SCOPES

    calls: list[str] = []

    class FakeManager:
        def status(self):
            calls.append("status")
            return ConnectionStatus(ConnectionState.CONNECTED, CALENDAR_SCOPES)

    monkeypatch.setattr(
        factory,
        "build_google_auth_manager_with_calendar",
        lambda: FakeManager(),
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--google-calendar", "status"])

    assert exit_info.value.code == 0
    assert calls == ["status"]
    assert capsys.readouterr().out.strip() == "Google Calendar conectado."
