"""Read-only installation checks. Never initialize models, audio or Tools."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import site
import subprocess
import sys

MODULES = (
    "jarvis", "jarvis.config", "ollama", "websockets", "PySide6.QtCore",
    "PySide6.QtWidgets", "ctranslate2", "faster_whisper", "torch",
    "openwakeword", "piper", "winrt.windows.media.control",
    "winrt.windows.foundation", "winrt.windows.foundation.collections",
    "winrt.windows.media", "winrt.windows.storage.streams",
)


def run(command: list[str], cwd: Path) -> dict:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=30, check=False)
        error = result.stderr.replace(str(Path.home()), "<HOME>")
        error = error.replace(str(cwd), "<CHECKOUT>").replace(sys.prefix, "<VENV>")
        output = result.stdout.replace(str(Path.home()), "<HOME>")
        output = output.replace(str(cwd), "<CHECKOUT>").replace(sys.prefix, "<VENV>")
        return {"exit": result.returncode, "stderr": error, "stdout": output}
    except subprocess.TimeoutExpired:
        return {"exit": 1, "error": "timeout_30s"}
    except OSError as error:
        return {"exit": 1, "error": type(error).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    report = {"python": platform.python_version(), "windows": platform.version(),
              "user_site": site.ENABLE_USER_SITE, "isolated": sys.flags.isolated,
              "venv": sys.prefix != sys.base_prefix}
    report["pip_check"] = run([sys.executable, "-I", "-m", "pip", "check"], checkout)
    report["help"] = run([sys.executable, "-I", "-m", "jarvis", "--help"], checkout)
    binding = (
        "from pathlib import Path; import jarvis; "
        "raise SystemExit(int(not Path(jarvis.__file__).resolve().is_relative_to("
        + repr(str(checkout / "src")) + ")))"
    )
    report["source_binding"] = run([sys.executable, "-I", "-c", binding], checkout)
    report["imports"] = {
        module: run([sys.executable, "-I", "-c", "import " + module], checkout)
        for module in MODULES
    }
    # Explicit sanitized settings: never use the host's data/settings.json.
    code = (
        "import json; from pathlib import Path; from jarvis.config import JarvisConfig; "
        "from jarvis.runtime.diagnostic import run_diagnostics, diagnostic_exit_code; "
        "r=run_diagnostics(JarvisConfig(settings_path=Path('docs/manual/settings.minimal.json'))); "
        "print(json.dumps({k:v['status'] for k,v in r.items() if isinstance(v,dict) and 'status' in v})); "
        "raise SystemExit(diagnostic_exit_code(r))"
    )
    report["doctor"] = run([sys.executable, "-I", "-c", code], checkout)
    report["versions"] = {
        name: importlib.metadata.version(name)
        for name in ("pip", "faster-whisper", "ctranslate2", "torch", "winrt-runtime")
    }
    print(json.dumps(report, ensure_ascii=True))
    checks = [report["pip_check"], report["help"], report["doctor"],
              report["source_binding"], *report["imports"].values()]
    return int(not valid_environment(site.ENABLE_USER_SITE, report["venv"], report["isolated"])
               or any(check["exit"] for check in checks))


def valid_environment(user_site: bool | None, venv: bool, isolated: int) -> bool:
    return user_site is False and venv and bool(isolated)


if __name__ == "__main__":
    raise SystemExit(main())
