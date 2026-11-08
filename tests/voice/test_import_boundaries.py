from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _run_fresh_import(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    src = str((Path.cwd() / "src").resolve())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        src if not existing else src + os.pathsep + existing
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_voice_confirmation_imports_in_fresh_interpreter() -> None:
    result = _run_fresh_import(
        "from jarvis.security.confirmation import VoiceConfirmation; "
        "print(VoiceConfirmation.__name__)"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "VoiceConfirmation"


def test_voice_package_keeps_lazy_pipeline_public_api() -> None:
    result = _run_fresh_import(
        "from jarvis.voice import VoicePipeline; "
        "print(VoicePipeline.__name__)"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "VoicePipeline"
