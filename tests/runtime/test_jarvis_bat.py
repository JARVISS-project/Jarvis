from pathlib import Path
import os
import shutil
import subprocess

import pytest


def test_launcher_is_location_independent_and_uses_runtime_mutex() -> None:
    text = Path("jarvis.bat").read_text(encoding="utf-8")
    assert "%~dp0" in text
    assert ".venv\\Scripts\\python.exe" in text
    assert '--runtime-root "%JARVIS_ROOT%."' in text
    assert "PYTHONUTF8=1" in text and "PYTHONIOENCODING=utf-8" in text
    assert 'PYTHONPATH=%JARVIS_ROOT%src;%PYTHONPATH%' in text
    assert 'if "%~1"==""' in text
    assert "--voice --ui" in text
    assert "start " not in text.lower()
    assert "exit /b %JARVIS_EXIT%" in text
    assert "LOCKFILE" not in text and "--startup" not in text


@pytest.mark.skipif(os.name != 'nt', reason='Windows batch launcher')
@pytest.mark.parametrize('exit_code', [0, 7])
def test_text_entry_calls_existing_launcher_from_another_directory(tmp_path, exit_code):
    program = tmp_path / 'program folder'
    program.mkdir()
    launcher = program / 'Abrir Jarvis.bat'
    shutil.copyfile('Abrir Jarvis.bat', launcher)
    (program / 'jarvis.bat').write_text(
        '@echo off\n> "%~dp0arguments.txt" echo %*\nexit /b ' + str(exit_code) + '\n',
        encoding='ascii',
    )
    result = subprocess.run(
        [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(launcher)],
        cwd=tmp_path, input='\n', text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    assert (program / 'arguments.txt').read_text().strip() == '--ui'
    assert ('nao iniciou' in result.stdout) is bool(exit_code)
