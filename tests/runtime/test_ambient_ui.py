from pathlib import Path
from typing import Any

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
