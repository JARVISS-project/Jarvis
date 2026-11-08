from pathlib import Path
from typing import Any

import pytest

from jarvis.runtime import ambient_ui


def test_launch_ambient_ui_uses_current_python_and_live_endpoint(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_popen(arguments: list[str], **kwargs: Any) -> object:
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(ambient_ui.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ambient_ui.sys, "executable", "python-from-venv")

    result = ambient_ui.launch_ambient_ui(Path("data/runtime/ambient-ui.json"))

    assert result is sentinel
    assert captured["arguments"] == [
        "python-from-venv",
        "-m",
        "jarvis.ui",
        "--live",
        "--endpoint",
        "data/runtime/ambient-ui.json",
    ]
    if ambient_ui.sys.platform == "win32":
        assert captured["kwargs"]["creationflags"] == (
            ambient_ui.subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        assert captured["kwargs"] == {}


def test_stop_spawned_ambient_ui_does_not_touch_finished_process() -> None:
    class FinishedProcess:
        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            raise AssertionError("Processo já encerrado não deve ser terminado")

    ambient_ui.stop_spawned_ambient_ui(FinishedProcess())  # type: ignore[arg-type]


@pytest.mark.parametrize('open_center', [False, True])
def test_control_center_option_preserves_bound_endpoint_and_process_group(monkeypatch, tmp_path, open_center):
    calls = []
    endpoint = tmp_path / 'pasta com espaços' / 'endpoint.json'
    monkeypatch.setattr(ambient_ui.subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)))
    ambient_ui.launch_ambient_ui(endpoint, open_control_center=open_center)
    arguments, kwargs = calls[0]
    assert arguments[arguments.index('--endpoint') + 1] == endpoint.as_posix()
    assert ('--control-center' in arguments) is open_center
    assert '--voice' not in arguments
    assert not kwargs.get('shell', False)


def test_packaged_ui_keeps_existing_child_protocol(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ambient_ui.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(ambient_ui.subprocess, 'Popen', lambda args, **kwargs: calls.append(args))
    ambient_ui.launch_ambient_ui(tmp_path / 'endpoint.json', open_control_center=True)
    assert '--ui-child' in calls[0]
    assert '--control-center' not in calls[0]  # Packaged entry already opens it.
