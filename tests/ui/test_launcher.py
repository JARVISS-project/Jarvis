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
