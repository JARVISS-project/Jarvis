"""Read-only Daily Driver readiness report."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib.util import find_spec
import subprocess
import sys

from jarvis.config import JarvisConfig
from jarvis.integrations.google.models import ConnectionState


class Readiness(str, Enum):
    READY = "READY"
    CONFIG_REQUIRED = "CONFIG_REQUIRED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    OPTIONAL_MISSING = "OPTIONAL_MISSING"


@dataclass(frozen=True, slots=True)
class DailyDriverCheck:
    name: str
    status: Readiness
    detail: str


def build_daily_driver_report(
    config: JarvisConfig,
    *,
    startup_command: str | None,
    google_calendar_state: ConnectionState,
    google_calendar_write_state: ConnectionState,
) -> tuple[DailyDriverCheck, ...]:
    vault = config.obsidian_vault_path
    browser_ready = config.browser_enabled and _playwright_browser_ready()
    checks: list[DailyDriverCheck] = [
        DailyDriverCheck(
            "audio",
            Readiness.READY if find_spec("sounddevice") else Readiness.CONFIG_REQUIRED,
            "sounddevice instalado" if find_spec("sounddevice") else "sounddevice ausente",
        ),
        DailyDriverCheck(
            "stt",
            Readiness.READY if find_spec("faster_whisper") else Readiness.CONFIG_REQUIRED,
            "faster-whisper instalado" if find_spec("faster_whisper") else "faster-whisper ausente",
        ),
        DailyDriverCheck(
            "wake_word",
            (
                Readiness.READY
                if find_spec("openwakeword")
                else Readiness.CONFIG_REQUIRED
            ),
            _wake_word_detail(config),
        ),
        DailyDriverCheck(
            "tts",
            Readiness.READY
            if _tts_ready(config)
            else Readiness.CONFIG_REQUIRED,
            _tts_detail(config),
        ),
        DailyDriverCheck(
            "obsidian",
            Readiness.READY
            if config.obsidian_enabled and vault is not None and vault.is_dir()
            else Readiness.CONFIG_REQUIRED,
            str(vault) if vault is not None else "vault não configurado",
        ),
        DailyDriverCheck(
            "desktop",
            Readiness.READY
            if config.desktop_read_enabled
            else Readiness.CONFIG_REQUIRED,
            (
                "read/control habilitados"
                if config.desktop_read_enabled and config.desktop_control_enabled
                else "leitura habilitada; controle desabilitado"
                if config.desktop_read_enabled
                else "desktop read desabilitado"
            ),
        ),
        DailyDriverCheck(
            "vision",
            Readiness.READY if config.vision_enabled else Readiness.CONFIG_REQUIRED,
            "habilitada" if config.vision_enabled else "desabilitada",
        ),
        DailyDriverCheck(
            "browser",
            Readiness.READY if browser_ready else Readiness.CONFIG_REQUIRED,
            "Playwright Chromium disponível e capability habilitada"
            if browser_ready
            else "browser capability desabilitada ou Chromium do Playwright ausente",
        ),
        DailyDriverCheck(
            "startup",
            Readiness.READY if startup_command else Readiness.CONFIG_REQUIRED,
            startup_command or "startup automático não instalado",
        ),
        DailyDriverCheck(
            "google_calendar_read",
            _google_readiness(google_calendar_state),
            google_calendar_state.value,
        ),
        DailyDriverCheck(
            "google_calendar_write",
            _google_readiness(google_calendar_write_state),
            google_calendar_write_state.value,
        ),
    ]
    return tuple(checks)


def render_daily_driver_report(checks: tuple[DailyDriverCheck, ...]) -> str:
    lines = ["JARVIS Daily Driver Check", ""]
    lines.extend(
        f"{check.name:.<28} {check.status.value:<16} {check.detail}"
        for check in checks
    )
    blockers = [
        check for check in checks
        if check.status in {Readiness.CONFIG_REQUIRED, Readiness.LOGIN_REQUIRED}
    ]
    lines.extend(
        [
            "",
            f"Blockers: {len(blockers)}",
            "Nenhuma ação foi executada; esta checagem é somente leitura.",
        ]
    )
    return "\n".join(lines)


def _playwright_browser_ready() -> bool:
    if find_spec("playwright") is None:
        return False
    probe = (
        "from pathlib import Path;"
        "from playwright.sync_api import sync_playwright;"
        "p=sync_playwright().start();"
        "path=Path(p.chromium.executable_path);"
        "p.stop();"
        "raise SystemExit(0 if path.is_file() else 1)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _google_readiness(state: ConnectionState) -> Readiness:
    if state is ConnectionState.CONNECTED:
        return Readiness.READY
    if state in {
        ConnectionState.DISCONNECTED,
        ConnectionState.AUTHORIZATION_REQUIRED,
        ConnectionState.REVOKED,
    }:
        return Readiness.LOGIN_REQUIRED
    return Readiness.CONFIG_REQUIRED


def _tts_ready(config: JarvisConfig) -> bool:
    provider = config.tts_provider
    if provider == "piper":
        return (
            config.tts_piper_model_path.is_file()
            and config.tts_piper_config_path.is_file()
        )
    if provider == "chatterbox":
        return find_spec("chatterbox") is not None
    if provider == "sapi":
        return find_spec("pyttsx3") is not None
    return (
        find_spec("pyttsx3") is not None
        or (
            config.tts_piper_model_path.is_file()
            and config.tts_piper_config_path.is_file()
        )
        or find_spec("chatterbox") is not None
    )


def _tts_detail(config: JarvisConfig) -> str:
    return f"provider={config.tts_provider}"



def _wake_word_detail(config: JarvisConfig) -> str:
    model = config.wake_word_model
    model_file = (
        str(config.wake_word_model_path)
        if config.wake_word_model_path is not None
        else "hey_jarvis_v0.1.onnx" if model == "hey_jarvis" else model
    )
    prefix = f"{model_file}; verifier externo "
    verifier_path = config.wake_word_verifier_path
    if find_spec("openwakeword") is None:
        return f"{prefix}indisponível; openwakeword ausente"

    if not config.wake_word_verifier_enabled:
        return (
            f"{prefix}desligado; threshold efetivo={config.wake_word_threshold:g}; "
            f"NPZ configurado={verifier_path}"
        )
    if not verifier_path.is_file():
        return (
            f"{prefix}ligado, artefato ausente -> baseline "
            f"threshold={config.wake_word_threshold:g}; NPZ={verifier_path}"
        )

    try:
        from jarvis.activation.verifier import load_safe_wake_verifier

        verifier = load_safe_wake_verifier(verifier_path)
    except (FileNotFoundError, OSError, ValueError) as error:
        return (
            f"{prefix}ligado, verifier inválido -> baseline "
            f"threshold={config.wake_word_threshold:g} "
            f"({type(error).__name__}); NPZ={verifier_path}"
        )

    if verifier.model_name != model:
        return (
            f"{prefix}ligado, verifier incompatível ({verifier.model_name}) -> "
            f"baseline threshold={config.wake_word_threshold:g}; NPZ={verifier_path}"
        )

    return (
        f"{prefix}ligado; verifier local NPZ={verifier_path}; "
        f"candidate={config.wake_word_verifier_candidate_threshold:g} "
        f"threshold efetivo={config.wake_word_verifier_threshold:g} "
        f"strong={config.wake_word_verifier_strong_threshold:g}"
    )
