"""Read-only runtime readiness diagnostic without model or effect initialization."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Literal
import urllib.error
import urllib.request

from jarvis.config import JarvisConfig
from jarvis.speech.gpu_budget import GpuBudgetManager
from jarvis.speech.profile import parse_stt_profile

Status = Literal["ready", "disabled", "unavailable", "degraded", "error"]


def _result(status: Status, **details: Any) -> dict[str, Any]:
    return {"status": status, **details}


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, AttributeError):
        return False


def check_gpu() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return _result("unavailable", backend="nvidia-smi", installed=False)
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return _result("error", backend="nvidia-smi", installed=True,
                       error=type(error).__name__)
    if completed.returncode != 0:
        return _result("degraded", backend="nvidia-smi", installed=True,
                       returncode=completed.returncode)
    devices = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return _result("ready" if devices else "degraded", backend="nvidia-smi",
                   installed=True, devices=devices,
                   process_memory_attribution=("unavailable_wddm"
                                               if sys.platform == "win32"
                                               else "available"))


def check_ollama() -> dict[str, Any]:
    installed = shutil.which("ollama") is not None
    request = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            available = response.status == 200
            return _result("ready" if available else "degraded", installed=installed,
                           backend_available=available, http_status=response.status)
    except urllib.error.HTTPError as error:
        return _result("degraded", installed=installed, backend_available=True,
                       http_status=error.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return _result("unavailable", installed=installed, backend_available=False)


def check_browser() -> dict[str, Any]:
    if sys.platform != "win32" or not _installed("winreg"):
        return _result("unavailable", installed=False, association=None)
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as key:
            association, _ = winreg.QueryValueEx(key, "ProgId")
    except FileNotFoundError:
        return _result("unavailable", installed=True, association=None)
    except OSError as error:
        return _result("error", installed=True, association=None,
                       error=type(error).__name__)
    return _result("ready", installed=True, association=str(association))


def check_stt(
    config: JarvisConfig, gpu_budget: GpuBudgetManager | None = None,
) -> dict[str, Any]:
    dependency = _installed("faster_whisper") and _installed("ctranslate2")
    enabled = config.stt_execution_mode == "process"
    profile = parse_stt_profile(config.stt_profile)
    budget = (gpu_budget or GpuBudgetManager()).assess(
        profile.minimum_free_vram_mib
    )
    status: Status = (
        "disabled" if not enabled else "unavailable" if not dependency
        else "ready" if budget.status == "ready" else "degraded"
        if budget.status == "insufficient" else "error"
    )
    return _result(status, enabled=enabled, dependency_installed=dependency,
                   backend_available=budget.status in {"ready", "insufficient"},
                   session_active=False, model=config.stt_model_normal,
                   device=config.stt_device_normal,
                   compute_type=config.stt_compute_type_normal,
                   execution_mode=config.stt_execution_mode,
                   vram_budget={
                       "status": budget.status,
                       "total_mib": budget.memory.total_mib if budget.memory else None,
                       "free_mib": budget.memory.free_mib if budget.memory else None,
                       "required_mib": budget.required_mib,
                   })


def check_tts(config: JarvisConfig) -> dict[str, Any]:
    providers = {"sapi": _installed("pyttsx3"), "piper": _installed("piper"),
                 "chatterbox": _installed("chatterbox")}
    installed = any(providers.values())
    status: Status = ("disabled" if not config.tts_enabled else
                      "ready" if installed else "unavailable")
    return _result(status, enabled=config.tts_enabled, provider=config.tts_provider,
                   dependency_installed=installed, providers=providers,
                   session_active=False)


def _optional_component(enabled: bool, module: str) -> dict[str, Any]:
    installed = _installed(module)
    status: Status = "disabled" if not enabled else ("ready" if installed else "unavailable")
    return _result(status, enabled=enabled, dependency_installed=installed,
                   backend_available=installed, session_active=False)


def check_apps() -> dict[str, dict[str, Any]]:
    candidates = {
        "spotify": Path(os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe")),
        "obsidian": Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\obsidian\Obsidian.exe")),
    }
    return {name: _result("ready" if path.is_file() else "unavailable",
                          installed=path.is_file()) for name, path in candidates.items()}


def check_gsmtc(enabled: bool) -> dict[str, Any]:
    """Probe the actual binding and current session without media metadata/effects."""
    from jarvis.media.service import GsmtcMediaService
    from jarvis.media.winrt_adapter import WinrtGsmtcAdapter

    adapter = WinrtGsmtcAdapter()
    if not enabled:
        return _result("disabled", enabled=False, session_active=False)
    if not adapter.available():
        return _result("unavailable", enabled=True, dependency_installed=False,
                       backend_available=False, reason="binding_missing")
    service = GsmtcMediaService(adapter)
    try:
        manager = service._call(adapter.discover_manager)
        session = service._call(lambda cancel: adapter.current_session(manager, cancel))
        return _result("ready", enabled=True, dependency_installed=True,
                       backend_available=True, session_active=session is not None,
                       reason="session_present" if session is not None else "no_session")
    except Exception as error:
        return _result("error", enabled=True, dependency_installed=True,
                       backend_available=False, error=type(error).__name__)


def check_core_audio(enabled: bool) -> dict[str, Any]:
    """The production backend uses ctypes/COM, not pycaw."""
    if not enabled:
        return _result("disabled", enabled=False, session_active=False)
    if sys.platform != "win32":
        return _result("unavailable", enabled=True, reason="windows_required")
    from jarvis.local_control.windows import _DefaultEndpointVolume

    try:
        with _DefaultEndpointVolume() as endpoint:
            endpoint.get()
        return _result("ready", enabled=True, dependency_installed=True,
                       backend_available=True, backend="ctypes-com", device_available=True)
    except (OSError, RuntimeError) as error:
        return _result("unavailable", enabled=True, backend="ctypes-com",
                       device_available=False, error=type(error).__name__)


def run_diagnostics(
    config: JarvisConfig | None = None,
    gpu_budget: GpuBudgetManager | None = None,
) -> dict[str, Any]:
    try:
        cfg = config or JarvisConfig()
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        return {"config": _result("error", error=type(error).__name__)}
    flags = {name: bool(getattr(cfg, name)) for name in (
        "semantic_read_tools_enabled", "local_control_enabled",
        "gsmtc_enabled", "safe_browser_enabled")}
    microphone_installed = _installed("pvrecorder")
    return {
        "python": _result("ready", version=sys.version.split()[0], executable=sys.executable),
        "config": _result("degraded" if cfg.settings_error else "ready",
                          error=cfg.settings_error),
        "gpu": check_gpu(), "stt": check_stt(cfg, gpu_budget), "ollama": check_ollama(),
        "microphone": _result("ready" if microphone_installed else "unavailable",
                              dependency_installed=microphone_installed,
                              session_active=False),
        "tts": check_tts(cfg), "browser": check_browser(),
        "gsmtc": check_gsmtc(cfg.gsmtc_enabled),
        "core_audio": check_core_audio(cfg.local_control_enabled),
        "apps": check_apps(), "flags": flags,
        "capabilities": {
            "enabled": sorted(name.removesuffix("_enabled")
                              for name, enabled in flags.items() if enabled),
            "disabled": sorted(name.removesuffix("_enabled")
                               for name, enabled in flags.items() if not enabled),
        },
    }


def render_diagnostic_report(report: dict[str, Any]) -> str:
    """Render a safe, actionable summary without exposing local paths or payloads."""
    lines = ["JARVIS Doctor (somente leitura)", ""]
    config = report.get("config", {"status": "error"})
    lines.append(f"Configuração........ {config.get('status', 'error')}")
    stt = report.get("stt", {})
    budget = stt.get("vram_budget", {})
    lines.append(
        "STT................. "
        f"{stt.get('status', 'error')} | {stt.get('model', 'desconhecido')} "
        f"/{stt.get('device', '?')}/{stt.get('compute_type', '?')} "
        f"/{stt.get('execution_mode', '?')}"
    )
    total, free, required = (budget.get("total_mib"), budget.get("free_mib"),
                             budget.get("required_mib"))
    if all(isinstance(value, int) for value in (total, free, required)):
        missing = max(0, required - free)
        lines.append(
            f"VRAM................ total={total} MiB | livre={free} MiB | "
            f"necessária={required} MiB | faltam={missing} MiB"
        )
        if missing:
            lines.extend([
                "Ação recomendada.... Feche aplicativos com aceleração gráfica ou reinicie,",
                "                     depois execute novamente: jarvis.bat --doctor",
                "Segurança........... Nenhum processo será encerrado automaticamente.",
            ])
    gpu = report.get("gpu", {})
    if gpu.get("process_memory_attribution") == "unavailable_wddm":
        lines.append(
            "Processos GPU........ Windows/WDDM não informa consumo confiável por processo; "
            "nenhum aplicativo foi responsabilizado."
        )
    labels = {
        "microphone": "Microfone", "tts": "TTS", "ollama": "Ollama",
        "browser": "Navegador", "gsmtc": "GSMTC", "core_audio": "Core Audio",
    }
    for key, label in labels.items():
        status = report.get(key, {}).get("status", "error")
        action = "pronto" if status == "ready" else (
            "habilite a flag/configuração" if status == "disabled"
            else "instale/configure a dependência e repita o diagnóstico"
        )
        lines.append(f"{label + '':20} {status:12} {action}")
    for app in ("spotify", "obsidian"):
        status = report.get("apps", {}).get(app, {}).get("status", "error")
        lines.append(f"{app.title():20} {status}")
    enabled = report.get("capabilities", {}).get("enabled", [])
    disabled = report.get("capabilities", {}).get("disabled", [])
    lines.append(f"Capabilities ON..... {', '.join(enabled) or 'nenhuma'}")
    lines.append(f"Capabilities OFF.... {', '.join(disabled) or 'nenhuma'}")
    lines.extend(["", "Nenhum modelo, áudio, Tool ou aplicativo foi iniciado."])
    return "\n".join(lines)


def diagnostic_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("config", {}).get("status") in {"degraded", "error"} or report.get(
        "stt", {}
    ).get("status") in {"degraded", "error", "unavailable"} else 0


def main() -> int:
    print(json.dumps(run_diagnostics(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
