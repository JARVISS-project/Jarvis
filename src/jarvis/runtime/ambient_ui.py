"""Gerenciamento do processo opcional da Ambient UI."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def launch_ambient_ui(endpoint_path: Path) -> subprocess.Popen[bytes]:
    """Abre a UI em outro processo, usando o mesmo Python/venv do runtime."""

    arguments = [
        sys.executable,
        "-m",
        "jarvis.ui",
        "--live",
        "--endpoint",
        endpoint_path.as_posix(),
    ]
    popen_kwargs: dict[str, int] = {}
    if sys.platform == "win32":
        # The HUD shares the console with the voice runtime. Put it in a new
        # process group so Ctrl+C used to stop Jarvis does not surface as a
        # KeyboardInterrupt traceback inside Qt's animation timer.
        process_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if process_group:
            popen_kwargs["creationflags"] = process_group
    return subprocess.Popen(arguments, **popen_kwargs)


def stop_spawned_ambient_ui(process: subprocess.Popen[bytes] | None) -> None:
    """Encerra somente a UI criada pelo runtime, sem afetar UIs manuais."""

    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)
