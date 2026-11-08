import builtins

import pytest

from jarvis.ui import launcher


def test_launcher_explains_how_to_install_pyside6(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "jarvis.ui.qt_app":
            error = ModuleNotFoundError("No module named 'PySide6'")
            error.name = "PySide6"
            raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match=r"\[ui\]"):
        launcher.run_ambient_ui_demo()


@pytest.mark.parametrize('open_control_center', [False, True])
def test_live_ui_receives_bound_send_command_and_microphone_settings(monkeypatch, tmp_path, open_control_center):
    from jarvis.application.state import AgentStateBus
    from jarvis.application.state_ipc import LocalStateClient, LocalStateServer
    from jarvis.ui import qt_app
    from tests.voice.test_ptt_integration import wait_for

    received = []
    endpoint_path = tmp_path / "ipc.json"
    server = LocalStateServer(AgentStateBus(), endpoint_path=endpoint_path,
                              command_callback=received.append)
    server.start()

    def run_ui(**kwargs):
        assert kwargs['open_control_center'] is open_control_center
        callback = kwargs["on_command"]
        assert isinstance(callback.__self__, LocalStateClient)
        assert callable(kwargs["microphone_saver"])
        wait_for(lambda: callback.__self__._socket is not None)
        callback({"command": "ptt_start"})
        callback({"command": "ptt_stop"})
        wait_for(lambda: len(received) == 2)
        return 0

    monkeypatch.setattr(qt_app, "run_qt_ambient_ui", run_ui)
    try:
        assert launcher.run_ambient_ui_live(endpoint_path, open_control_center=open_control_center) == 0
        assert received == [{"command": "ptt_start"}, {"command": "ptt_stop"}]
    finally:
        server.close()


@pytest.mark.parametrize('flag', [False, True])
def test_live_cli_preserves_explicit_control_center_option(monkeypatch, tmp_path, flag):
    from jarvis.ui import __main__ as cli
    calls = []
    monkeypatch.setattr(cli, 'run_ambient_ui_live', lambda path, **kwargs: calls.append((path, kwargs)) or 0)
    args = ['--live', '--endpoint', str(tmp_path/'endpoint.json')]
    if flag:
        args.append('--control-center')
    with pytest.raises(SystemExit) as result:
        cli.main(args)
    assert result.value.code == 0
    assert calls == [(tmp_path/'endpoint.json', {'open_control_center': flag})]
