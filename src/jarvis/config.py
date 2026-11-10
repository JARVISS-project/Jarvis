from dataclasses import dataclass, field
import json
import os
import re
from pathlib import Path
import shutil
import tempfile
import sys
from typing import Any

from jarvis.speech.profile import parse_stt_profile


def _environment_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on", "sim"}


def _load_local_settings(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, f"Configuração local inválida: {type(error).__name__}."
    if not isinstance(value, dict):
        return {}, "Configuração local inválida: objeto JSON esperado."
    validators: dict[str, type] = {
        "obsidian_vault": str,
        "obsidian_write": bool,
        "tts_provider": str,
        "tts_reference_audio": str,
        "tts_piper_pace": str,
        "tts_piper_voice": str,
        "tts_enabled": bool,
        "assistant_tone": str,
        "assistant_detail": str,
        "assistant_opinions": bool,
        "assistant_address_mode": str,
        "user_display_name": str,
        "agent_name": str,
        "desktop_read": bool,
        "desktop_control": bool,
        "vision": bool,
        "browser": bool,
        "google_calendar": bool,
        "google_calendar_write": bool,
        "google_calendar_authorized_calendars": dict,
        "google_calendar_write_calendars": dict,
        "google_drive_read": bool,
        "google_docs_read": bool,
        "google_drive_docs_files": dict,
        "google_drive_docs_folders": dict,
        "google_drive_docs_grant": str,
        "google_drive_docs_account": str,
        "google_drive_docs_client_config": str,
        "briefing_enabled": bool,
        "briefing_auto_enabled": bool,
        "briefing_timezone": str,
        "briefing_calendar_alias": str,
        "briefing_quiet_start": str,
        "briefing_quiet_end": str,
        "microphone_device_name": str,
        "wake_word_verifier_enabled": bool,
        "stt_profile": str,
        "stt_execution_mode": str,
        "semantic_read_tools_enabled": bool,
        "local_control_enabled": bool,
        "application_catalog_enabled": bool,
        "gsmtc_enabled": bool,
        "safe_browser_enabled": bool,
    }
    for key, expected_type in validators.items():
        current = value.get(key)
        if current is not None and not isinstance(current, expected_type):
            return {}, f"Configuração local inválida: {key}."
    return {
        key: value[key]
        for key in validators
        if key in value
    }, None


@dataclass
class JarvisConfig:
    model_name: str = "qwen3:4b-instruct-2507-q4_K_M"
    conversation_max_messages: int = 20
    assistant_address_mode: str = "senhor"
    assistant_response_style: str = "natural"
    assistant_tone: str = "equilibrado"
    assistant_detail: str = "curtas"
    assistant_opinions: bool = True
    user_display_name: str = ""
    agent_name: str = "Jarvis"
    database_path: Path = Path("data/jarvis.db")
    audit_log_path: Path = Path("logs/audit.log")
    filesystem_base_directory: Path = field(default_factory=lambda: Path.cwd() / 'documents' if getattr(sys, 'frozen', False) else Path.cwd())
    settings_path: Path = Path("data/settings.json")
    skill_store_path: Path = Path("data/skills.json")
    skills_obsidian_mirror_enabled: bool = True
    skills_obsidian_mirror_directory: str = "Jarvis/Skills"
    obsidian_enabled: bool | None = None
    obsidian_vault_path: Path | None = None
    filesystem_read_enabled: bool = True
    filesystem_write_enabled: bool = True
    obsidian_read_enabled: bool = True
    obsidian_write_enabled: bool | None = None
    desktop_read_enabled: bool = False
    desktop_control_enabled: bool = False
    vision_enabled: bool = False
    browser_enabled: bool = False
    google_calendar_enabled: bool = False
    google_calendar_authorized_calendars: dict[str, str] = field(default_factory=dict)
    google_calendar_write_enabled: bool = False
    google_calendar_write_calendars: dict[str, str] = field(default_factory=dict)
    google_drive_read_enabled: bool = False
    google_docs_read_enabled: bool = False
    google_drive_docs_files: dict[str, str] = field(default_factory=dict)
    google_drive_docs_folders: dict[str, str] = field(default_factory=dict)
    google_drive_docs_grant: str = ''
    briefing_enabled: bool = True
    briefing_auto_enabled: bool = False
    briefing_timezone: str = 'America/Sao_Paulo'
    briefing_calendar_alias: str = 'principal'
    briefing_quiet_start: str = '22:00'
    briefing_quiet_end: str = '07:00'
    briefing_marker_path: Path = Path('data/briefing-marker.sqlite3')
    browser_allowed_domains: list[str] = field(default_factory=list)
    browser_blocked_domains: list[str] = field(default_factory=list)
    browser_max_tabs: int = 10
    obsidian_search_max_results: int = 10
    obsidian_max_note_size_bytes: int = 1_000_000
    max_tool_iterations: int = 5
    ollama_keep_alive_normal: str = "5m"
    ollama_keep_alive_game_mode: str = "30s"
    ollama_context_window: int = 8192
    llm_thinking_enabled: bool = False
    llm_warm_up_on_voice_start: bool = True
    llm_debug_enabled: bool = field(
        default_factory=lambda: _environment_flag("JARVIS_LLM_DEBUG", False)
    )
    wake_word_enabled: bool = False
    # "Hey Jarvis" é a chamada oficial e corresponde diretamente ao modelo
    # local ``hey_jarvis`` do openWakeWord. Sensibilidade deve ser ajustada por
    # benchmark real, não por redução indiscriminada de threshold.
    wake_word_phrase: str = "Hey Jarvis"
    wake_word_model: str = "hey_jarvis"
    wake_word_model_path: Path | None = None
    wake_word_threshold: float = 0.5
    wake_word_verifier_enabled: bool = False
    # Verifier local opcional e específico desta instalação/voz. O artefato
    # numérico fica fora do Git e só é usado quando o arquivo existe.
    wake_word_verifier_path: Path = Path(
        "data/voice/hey_jarvis_verifier.npz"
    )
    wake_word_verifier_candidate_threshold: float = 0.12
    wake_word_verifier_threshold: float = 0.74
    wake_word_verifier_strong_threshold: float = 0.85
    wake_min_consecutive_detections: int = 2
    wake_word_cooldown_seconds: float = 1.5
    ambient_ui_endpoint_path: Path = Path("data/runtime/ambient-ui.json")
    microphone_device_name: str | None = None
    microphone_device_index: int | None = None
    stt_model: str = "small"
    stt_profile: str = "medium"
    stt_execution_mode: str = "legacy"
    semantic_read_tools_enabled: bool = False
    local_control_enabled: bool = False
    application_catalog_enabled: bool = True
    gsmtc_enabled: bool = False
    safe_browser_enabled: bool = False
    stt_model_normal: str = "medium"
    stt_model_game_mode: str = "small"
    stt_language: str | None = "pt"
    # Um prompt textual fixo pode ser ecoado pelo Whisper em áudio curto/ambíguo.
    # O idioma já é fixado em pt e os termos úteis entram via hotwords dinâmicas.
    stt_initial_prompt: str | None = None
    stt_hotwords_enabled: bool = True
    stt_vad_enabled: bool = True
    stt_vad_min_silence_duration_ms: int = 500
    stt_vad_speech_pad_ms: int = 200
    stt_vad_threshold: float = 0.5
    stt_warm_up_on_voice_start: bool = True
    stt_preload_timeout_seconds: float = 30.0
    stt_ptbr_fallback_enabled: bool = field(
        default_factory=lambda: _environment_flag(
            "JARVIS_STT_PTBR_FALLBACK", not getattr(sys, 'frozen', False)
        )
    )
    stt_ptbr_fallback_model: str = (
        "lgris/wav2vec2-large-xlsr-open-brazilian-portuguese-v2"
    )
    stt_ptbr_fallback_min_confidence: float = 0.96
    stt_ptbr_fallback_min_margin: float = 6.5
    stt_ptbr_fallback_startup_timeout_seconds: float = 45.0
    stt_ptbr_fallback_response_timeout_seconds: float = 10.0
    voice_recognition_metrics_enabled: bool = field(
        default_factory=lambda: _environment_flag("JARVIS_VOICE_METRICS", True)
    )
    voice_recognition_metrics_path: Path = Path(
        "data/voice/recognition_metrics.jsonl"
    )
    stt_device_normal: str = "auto"
    stt_compute_type_normal: str = "auto"
    stt_device_game_mode: str = "cpu"
    stt_compute_type_game_mode: str = "int8"
    voice_silence_threshold: float = 500.0
    voice_silence_duration_seconds: float = 0.8
    voice_end_silence_seconds: float = 1.2
    voice_incomplete_turn_extension_seconds: float = 2.0
    voice_max_recording_seconds: float = 15.0
    voice_pre_speech_timeout_seconds: float = 5.0
    voice_minimum_speech_duration_seconds: float = 0.35
    voice_minimum_audio_energy: float = 700.0
    voice_noise_calibration_seconds: float = 0.3
    voice_noise_margin: float = 300.0
    voice_maximum_speech_threshold: float = 5000.0
    voice_pre_roll_seconds: float = 0.25
    voice_confirmation_enabled: bool = True
    voice_confirmation_timeout_seconds: float = 15.0
    voice_confirmation_retry_timeout_seconds: float = 10.0
    voice_confirmation_min_speech_duration_seconds: float = 0.15
    voice_confirmation_keyboard_fallback: bool = False
    voice_repair_timeout_seconds: float = 8.0
    voice_post_tts_grace_seconds: float = 0.75
    # Wake acknowledgement confirms presence immediately after "Hey Jarvis".
    voice_wake_acknowledgement_enabled: bool = True
    # Per-command acknowledgement is optional; wake acknowledgement already
    # provides conversational feedback and avoids repetitive "Entendi" chatter.
    voice_acknowledgement_enabled: bool = False
    voice_debug_enabled: bool = field(
        default_factory=lambda: _environment_flag("JARVIS_VOICE_DEBUG", False)
    )
    latency_debug_enabled: bool = field(
        default_factory=lambda: _environment_flag("JARVIS_LATENCY_DEBUG", False)
    )
    voice_cancel_max_no_speech_probability: float = 0.35
    voice_cancel_min_average_log_probability: float = -0.6
    voice_cancel_min_speech_duration_seconds: float = 0.2
    conversation_followup_enabled: bool = True
    conversation_followup_timeout_seconds: float = 15.0
    conversation_question_timeout_seconds: float = 20.0
    conversation_followup_timeout_game_mode_seconds: float = 4.0
    pending_action_timeout_seconds: float = 30.0
    confirmation_first_reminder_seconds: float = 25.0
    confirmation_reminder_interval_seconds: float = 30.0
    confirmation_max_reminders: int = 2
    confirmation_session_timeout_seconds: float = 120.0
    # Full-duplex barge-in is experimental without acoustic echo cancellation.
    # Keep it disabled by default so Jarvis cannot transcribe its own speaker.
    barge_in_enabled: bool = field(
        default_factory=lambda: _environment_flag("JARVIS_BARGE_IN_ENABLED", False)
    )
    barge_in_energy_margin: float = 500.0
    barge_in_min_speech_duration_seconds: float = 0.18
    barge_in_grace_period_seconds: float = 0.2
    tts_enabled: bool = True
    tts_enabled_game_mode: bool = True
    tts_provider: str = "auto"
    tts_device: str = "auto"
    tts_language_id: str = "pt"
    tts_reference_audio_path: Path | None = None
    tts_piper_model_path: Path = field(default_factory=lambda: Path('models' if getattr(sys, 'frozen', False) else 'data') / 'voices/razo/pt-BR-razo-medium.onnx')
    tts_piper_config_path: Path = field(default_factory=lambda: Path('models' if getattr(sys, 'frozen', False) else 'data') / 'voices/razo/pt-BR-razo-medium.onnx.json')
    # Keep enough drain tail for Windows audio devices to avoid clipping the
    # final phoneme. The conversational profiles intentionally favor a calmer
    # cadence over maximum throughput.
    tts_piper_tail_silence_seconds: float = 0.30
    # Piper length scale: > 1 speaks more slowly. These values preserve the
    # slower cadence previously preferred for Razo in real voice sessions.
    tts_piper_pace: str = "natural"
    tts_piper_sentence_silence_seconds: float = 0.18
    tts_chatterbox_t3_model: str = "v3"
    tts_chatterbox_exaggeration: float = 0.35
    tts_chatterbox_cfg_weight: float = 0.35
    tts_chatterbox_fallback_to_sapi: bool = True
    tts_rate: int = 180
    tts_volume: float = 1.0
    tts_voice_id: str | None = None
    tts_post_speech_cooldown_seconds: float = 0.75
    settings_error: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        settings, self.settings_error = _load_local_settings(
            self.settings_path
        )
        for key, choices in (("assistant_tone", {"direto", "equilibrado", "descontraido"}),
                             ("assistant_detail", {"curtas", "detalhadas"})):
            if key in settings:
                if settings[key] in choices:
                    setattr(self, key, settings[key])
                else:
                    self.settings_error = f"Configuração local inválida: {key}."
        if "assistant_opinions" in settings:
            self.assistant_opinions = settings["assistant_opinions"]
        if "tts_enabled" in settings:
            self.tts_enabled = settings["tts_enabled"]
        if settings.get("tts_piper_voice"):
            from jarvis.tts.voices import installed_voices
            chosen = next((v for v in installed_voices(self)
                           if v.id == settings["tts_piper_voice"]), None)
            if chosen:
                self.tts_piper_model_path, self.tts_piper_config_path = chosen.model, chosen.config
            else:
                self.settings_error = "Voz selecionada indisponível; nenhum modelo baixado."
                # Preserve the selected identity; do not silently switch voices.
                self.tts_piper_model_path = Path("data/voices/.unavailable.onnx")
                self.tts_piper_config_path = Path("data/voices/.unavailable.onnx.json")
        from datetime import time
        from zoneinfo import ZoneInfo
        for key in ('briefing_enabled', 'briefing_auto_enabled', 'briefing_timezone',
                    'briefing_calendar_alias', 'briefing_quiet_start', 'briefing_quiet_end'):
            if key in settings:
                setattr(self, key, settings[key])
        ZoneInfo(self.briefing_timezone)
        time.fromisoformat(self.briefing_quiet_start)
        time.fromisoformat(self.briefing_quiet_end)
        if "wake_word_verifier_enabled" in settings:
            self.wake_word_verifier_enabled = settings[
                "wake_word_verifier_enabled"
            ]
        profile_name = os.getenv("JARVIS_STT_PROFILE") or settings.get(
            "stt_profile", self.stt_profile
        )
        profile = parse_stt_profile(str(profile_name))
        self.stt_profile = profile.name
        self.stt_model_normal = profile.model
        self.stt_device_normal = profile.device
        self.stt_compute_type_normal = profile.compute_type
        execution_mode = str(
            os.getenv("JARVIS_STT_EXECUTION_MODE")
            or settings.get("stt_execution_mode", self.stt_execution_mode)
        ).strip().casefold()
        if execution_mode not in {"process", "legacy"}:
            raise ValueError(
                "Modo de execução STT inválido. Esperado: process ou legacy."
            )
        self.stt_execution_mode = execution_mode
        self.semantic_read_tools_enabled = _environment_flag(
            "JARVIS_SEMANTIC_READ_TOOLS_ENABLED",
            settings.get(
                "semantic_read_tools_enabled",
                self.semantic_read_tools_enabled,
            ),
        )
        self.local_control_enabled = _environment_flag(
            "JARVIS_LOCAL_CONTROL_ENABLED",
            settings.get("local_control_enabled", self.local_control_enabled),
        )
        self.application_catalog_enabled = _environment_flag(
            "JARVIS_APPLICATION_CATALOG_ENABLED",
            settings.get("application_catalog_enabled", self.application_catalog_enabled),
        )
        self.gsmtc_enabled = _environment_flag(
            "JARVIS_GSMTC_ENABLED",
            settings.get("gsmtc_enabled", self.gsmtc_enabled),
        )
        self.safe_browser_enabled = _environment_flag(
            "JARVIS_SAFE_BROWSER_ENABLED",
            settings.get("safe_browser_enabled", self.safe_browser_enabled),
        )
        environment_vault = os.getenv("JARVIS_OBSIDIAN_VAULT")
        if environment_vault:
            self.obsidian_vault_path = Path(environment_vault).expanduser()
        elif self.obsidian_vault_path is None:
            local_vault = settings.get("obsidian_vault")
            if isinstance(local_vault, str) and local_vault.strip():
                self.obsidian_vault_path = Path(local_vault).expanduser()

        if self.obsidian_enabled is None:
            self.obsidian_enabled = self.obsidian_vault_path is not None

        environment_provider = os.getenv("JARVIS_TTS_PROVIDER")
        local_provider = settings.get("tts_provider")
        provider = environment_provider or local_provider or self.tts_provider
        self.tts_provider = str(provider).casefold().strip()
        if self.tts_provider not in {"auto", "sapi", "piper", "chatterbox"}:
            self.settings_error = "Configuração local inválida: tts_provider."
            self.tts_provider = "auto"

        environment_pace = os.getenv("JARVIS_TTS_PIPER_PACE")
        local_pace = settings.get("tts_piper_pace")
        pace = environment_pace or local_pace or self.tts_piper_pace
        self.tts_piper_pace = str(pace).casefold().strip()
        if self.tts_piper_pace not in {"fast", "natural", "calm"}:
            self.settings_error = "Configuração local inválida: tts_piper_pace."
            self.tts_piper_pace = "natural"

        environment_reference = os.getenv("JARVIS_TTS_REFERENCE_AUDIO")
        local_reference = settings.get("tts_reference_audio")
        reference = environment_reference or local_reference
        if reference and self.tts_reference_audio_path is None:
            self.tts_reference_audio_path = Path(str(reference)).expanduser()

        environment_treatment = os.getenv("JARVIS_ASSISTANT_ADDRESS_MODE")
        local_treatment = settings.get("assistant_address_mode")
        treatment = environment_treatment or local_treatment
        if treatment:
            self.assistant_address_mode = str(treatment).casefold().strip()
        if self.assistant_address_mode not in {"senhor", "natural", "direto"}:
            self.settings_error = (
                "Configuração local inválida: assistant_address_mode."
            )
            self.assistant_address_mode = "senhor"

        environment_user_name = os.getenv("JARVIS_USER_DISPLAY_NAME")
        local_user_name = settings.get("user_display_name")
        configured_user_name = environment_user_name or local_user_name
        if configured_user_name:
            self.user_display_name = str(configured_user_name).strip()
        if not self.user_display_name:
            detected_user_name = (
                os.getenv("USERNAME")
                or os.getenv("USER")
                or "Usuário"
            ).strip()
            self.user_display_name = (
                detected_user_name.title()
                if detected_user_name.islower()
                else detected_user_name
            )

        environment_agent_name = os.getenv("JARVIS_AGENT_NAME")
        local_agent_name = settings.get("agent_name")
        configured_agent_name = environment_agent_name or local_agent_name
        if configured_agent_name:
            self.agent_name = str(configured_agent_name).strip()
        if not self.agent_name:
            self.agent_name = "Jarvis"

        environment_write = os.getenv("JARVIS_OBSIDIAN_WRITE")
        if environment_write is not None:
            self.obsidian_write_enabled = _environment_flag(
                "JARVIS_OBSIDIAN_WRITE"
            )
        elif self.obsidian_write_enabled is None:
            local_write = settings.get("obsidian_write", False)
            self.obsidian_write_enabled = (
                local_write if isinstance(local_write, bool) else False
            )

        environment_desktop_read = os.getenv("JARVIS_DESKTOP_READ")
        if environment_desktop_read is not None:
            self.desktop_read_enabled = _environment_flag(
                "JARVIS_DESKTOP_READ"
            )
        else:
            local_desktop_read = settings.get("desktop_read")
            if isinstance(local_desktop_read, bool):
                self.desktop_read_enabled = local_desktop_read

        environment_desktop_control = os.getenv("JARVIS_DESKTOP_CONTROL")
        if environment_desktop_control is not None:
            self.desktop_control_enabled = _environment_flag(
                "JARVIS_DESKTOP_CONTROL"
            )
        else:
            local_desktop_control = settings.get("desktop_control")
            if isinstance(local_desktop_control, bool):
                self.desktop_control_enabled = local_desktop_control

        environment_vision = os.getenv("JARVIS_VISION")
        if environment_vision is not None:
            self.vision_enabled = _environment_flag("JARVIS_VISION")
        else:
            local_vision = settings.get("vision")
            if isinstance(local_vision, bool):
                self.vision_enabled = local_vision

        environment_browser = os.getenv("JARVIS_BROWSER")
        if environment_browser is not None:
            self.browser_enabled = _environment_flag("JARVIS_BROWSER")
        else:
            local_browser = settings.get("browser")
            if isinstance(local_browser, bool):
                self.browser_enabled = local_browser

        environment_calendar = os.getenv("JARVIS_GOOGLE_CALENDAR")
        for attribute, key, limit in (
            ('google_drive_docs_files', 'google_drive_docs_files', 20),
            ('google_drive_docs_folders', 'google_drive_docs_folders', 10),
        ):
            mapping = settings.get(key, getattr(self, attribute))
            if (not isinstance(mapping, dict) or len(mapping) > limit
                    or any(not isinstance(a, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', a)
                           or a == 'autorizados' or not isinstance(v, str)
                           or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', v)
                           for a, v in mapping.items())):
                raise ValueError('Allowlist Drive/Docs inválida.')
            setattr(self, attribute, dict(mapping))
        grant = settings.get('google_drive_docs_grant', self.google_drive_docs_grant)
        if not isinstance(grant, str) or (grant and not re.fullmatch(r'[a-f0-9]{64}', grant)):
            raise ValueError('Vínculo Drive/Docs inválido.')
        self.google_drive_docs_grant = grant
        for attribute, key, variable in (
            ('google_drive_read_enabled', 'google_drive_read', 'JARVIS_GOOGLE_DRIVE_READ'),
            ('google_docs_read_enabled', 'google_docs_read', 'JARVIS_GOOGLE_DOCS_READ'),
        ):
            if os.getenv(variable) is not None:
                setattr(self, attribute, _environment_flag(variable))
            elif isinstance(settings.get(key), bool):
                setattr(self, attribute, settings[key])
        calendars = settings.get("google_calendar_authorized_calendars", self.google_calendar_authorized_calendars)
        if (not isinstance(calendars, dict) or len(calendars) > 10
                or any(not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", alias)
                       or not isinstance(value, str) or not value or len(value) > 256
                       or any(ord(char) < 32 for char in value)
                       for alias, value in calendars.items())):
            raise ValueError("Calendários autorizados inválidos.")
        self.google_calendar_authorized_calendars = dict(calendars)
        write_calendars = settings.get("google_calendar_write_calendars", self.google_calendar_write_calendars)
        if (not isinstance(write_calendars, dict) or len(write_calendars) > 10
                or any(not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", alias)
                       or not isinstance(value, str) or not value or len(value) > 256
                       or any(ord(char) < 32 for char in value)
                       for alias, value in write_calendars.items())):
            raise ValueError("Calendar WRITE allowlist invalida.")
        self.google_calendar_write_calendars = dict(write_calendars)
        if environment_calendar is not None:
            self.google_calendar_enabled = _environment_flag("JARVIS_GOOGLE_CALENDAR")
        else:
            local_calendar = settings.get("google_calendar")
            if isinstance(local_calendar, bool):
                self.google_calendar_enabled = local_calendar

        environment_calendar_write = os.getenv("JARVIS_GOOGLE_CALENDAR_WRITE")
        if environment_calendar_write is not None:
            self.google_calendar_write_enabled = _environment_flag("JARVIS_GOOGLE_CALENDAR_WRITE")
        else:
            local_calendar_write = settings.get("google_calendar_write")
            if isinstance(local_calendar_write, bool):
                self.google_calendar_write_enabled = local_calendar_write

        environment_browser_domains = os.getenv("JARVIS_BROWSER_ALLOWED_DOMAINS")
        if environment_browser_domains is not None:
            self.browser_allowed_domains = [
                d.strip().lower() for d in environment_browser_domains.split(",") if d.strip()
            ]
        else:
            local_browser_domains = settings.get("browser_allowed_domains")
            if isinstance(local_browser_domains, list):
                self.browser_allowed_domains = [d.strip().lower() for d in local_browser_domains if d.strip()]

        environment_browser_blocked = os.getenv("JARVIS_BROWSER_BLOCKED_DOMAINS")
        if environment_browser_blocked is not None:
            self.browser_blocked_domains = [
                d.strip().lower() for d in environment_browser_blocked.split(",") if d.strip()
            ]
        else:
            local_browser_blocked = settings.get("browser_blocked_domains")
            if isinstance(local_browser_blocked, list):
                self.browser_blocked_domains = [d.strip().lower() for d in local_browser_blocked if d.strip()]

        environment_browser_max_tabs = os.getenv("JARVIS_BROWSER_MAX_TABS")
        if environment_browser_max_tabs is not None:
            try:
                self.browser_max_tabs = int(environment_browser_max_tabs)
            except ValueError:
                pass
        else:
            local_browser_max_tabs = settings.get("browser_max_tabs")
            if isinstance(local_browser_max_tabs, int):
                self.browser_max_tabs = local_browser_max_tabs

        environment_mic_name = os.getenv("JARVIS_MICROPHONE_NAME")
        if environment_mic_name is not None:
            self.microphone_device_name = environment_mic_name
        else:
            local_mic_name = settings.get("microphone_device_name")
            if isinstance(local_mic_name, str):
                self.microphone_device_name = local_mic_name

        environment_mic_index = os.getenv("JARVIS_MICROPHONE_INDEX")
        if environment_mic_index is not None:
            try:
                self.microphone_device_index = int(environment_mic_index)
            except ValueError:
                pass

    def save_microphone_settings(self, device_name: str | None) -> None:
        settings, _error = _load_local_settings(self.settings_path)
        if device_name is not None:
            settings["microphone_device_name"] = device_name
        else:
            settings.pop("microphone_device_name", None)
        self._write_local_settings(settings)
        if os.getenv("JARVIS_MICROPHONE_NAME") is None:
            self.microphone_device_name = device_name

    def save_stt_profile(
        self,
        profile_name: str,
        *,
        execution_mode: str | None = None,
        backup_path: Path | None = None,
    ) -> None:
        profile = parse_stt_profile(profile_name)
        normalized_mode = (
            self.stt_execution_mode if execution_mode is None
            else execution_mode.strip().casefold()
        )
        if normalized_mode not in {"process", "legacy"}:
            raise ValueError(
                "Modo de execução STT inválido. Esperado: process ou legacy."
            )
        settings, error = _load_local_settings(self.settings_path)
        if error is not None:
            raise ValueError(error)
        if backup_path is not None and self.settings_path.exists():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.settings_path, backup_path)
        # Validate known fields above, preserve integration/extension keys too.
        if self.settings_path.exists():
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        settings["stt_profile"] = profile.name
        settings["stt_execution_mode"] = normalized_mode
        self._write_local_settings(settings)
        if os.getenv("JARVIS_STT_PROFILE") is None:
            self.stt_profile = profile.name
            self.stt_model_normal = profile.model
            self.stt_device_normal = profile.device
            self.stt_compute_type_normal = profile.compute_type
        if os.getenv("JARVIS_STT_EXECUTION_MODE") is None:
            self.stt_execution_mode = normalized_mode

    def save_semantic_read_tools_enabled(
        self,
        enabled: bool,
        *,
        backup_path: Path | None = None,
    ) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("A flag semântica read-only deve ser booleana.")
        settings, error = _load_local_settings(self.settings_path)
        if error is not None:
            raise ValueError(error)
        if backup_path is not None and self.settings_path.exists():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.settings_path, backup_path)
        settings["semantic_read_tools_enabled"] = enabled
        self._write_local_settings(settings)
        if os.getenv("JARVIS_SEMANTIC_READ_TOOLS_ENABLED") is None:
            self.semantic_read_tools_enabled = enabled

    def save_local_control_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("A flag de controle local deve ser booleana.")
        settings, error = _load_local_settings(self.settings_path)
        if error is not None:
            raise ValueError(error)
        settings["local_control_enabled"] = enabled
        self._write_local_settings(settings)
        if os.getenv("JARVIS_LOCAL_CONTROL_ENABLED") is None:
            self.local_control_enabled = enabled

    def save_gsmtc_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("A flag GSMTC deve ser booleana.")
        settings, error = _load_local_settings(self.settings_path)
        if error is not None:
            raise ValueError(error)
        settings["gsmtc_enabled"] = enabled
        self._write_local_settings(settings)
        if os.getenv("JARVIS_GSMTC_ENABLED") is None:
            self.gsmtc_enabled = enabled

    def save_safe_browser_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("A flag de navegador seguro deve ser booleana.")
        settings, error = _load_local_settings(self.settings_path)
        if error is not None:
            raise ValueError(error)
        settings["safe_browser_enabled"] = enabled
        self._write_local_settings(settings)
        if os.getenv("JARVIS_SAFE_BROWSER_ENABLED") is None:
            self.safe_browser_enabled = enabled

    def save_obsidian_settings(
        self,
        *,
        vault: Path | None = None,
        write: bool | None = None,
    ) -> None:
        settings, _error = _load_local_settings(self.settings_path)
        if vault is not None:
            settings["obsidian_vault"] = str(vault)
        if write is not None:
            settings["obsidian_write"] = write
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.settings_error = None
        if vault is not None and not os.getenv("JARVIS_OBSIDIAN_VAULT"):
            self.obsidian_vault_path = vault
            self.obsidian_enabled = True
        if write is not None and os.getenv("JARVIS_OBSIDIAN_WRITE") is None:
            self.obsidian_write_enabled = write


    def save_desktop_settings(
        self,
        *,
        read: bool | None = None,
        control: bool | None = None,
    ) -> None:
        settings, _error = _load_local_settings(self.settings_path)
        if read is not None:
            settings["desktop_read"] = bool(read)
        if control is not None:
            settings["desktop_control"] = bool(control)
        self._write_local_settings(settings)
        if read is not None and os.getenv("JARVIS_DESKTOP_READ") is None:
            self.desktop_read_enabled = bool(read)
        if control is not None and os.getenv("JARVIS_DESKTOP_CONTROL") is None:
            self.desktop_control_enabled = bool(control)
    def save_vision_settings(self, *, enabled: bool) -> None:
        settings, _error = _load_local_settings(self.settings_path)
        settings["vision"] = bool(enabled)
        self._write_local_settings(settings)
        if os.getenv("JARVIS_VISION") is None:
            self.vision_enabled = bool(enabled)

    def save_browser_settings(
        self,
        *,
        enabled: bool | None = None,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        max_tabs: int | None = None,
    ) -> None:
        settings, _error = _load_local_settings(self.settings_path)
        if enabled is not None:
            settings["browser"] = bool(enabled)
        if allowed_domains is not None:
            settings["browser_allowed_domains"] = [d.strip().lower() for d in allowed_domains if d.strip()]
        if blocked_domains is not None:
            settings["browser_blocked_domains"] = [d.strip().lower() for d in blocked_domains if d.strip()]
        if max_tabs is not None:
            settings["browser_max_tabs"] = int(max_tabs)
        self._write_local_settings(settings)
        if os.getenv("JARVIS_BROWSER") is None and enabled is not None:
            self.browser_enabled = bool(enabled)
        if allowed_domains is not None:
            self.browser_allowed_domains = [d.strip().lower() for d in allowed_domains if d.strip()]
        if blocked_domains is not None:
            self.browser_blocked_domains = [d.strip().lower() for d in blocked_domains if d.strip()]
        if max_tabs is not None:
            self.browser_max_tabs = int(max_tabs)

    def save_calendar_settings(self, *, enabled: bool) -> None:
        settings, error = _load_local_settings(self.settings_path)
        if error or type(enabled) is not bool:
            raise ValueError('Configuração Calendar inválida; não ampliar acesso.')
        settings["google_calendar"] = bool(enabled)
        self._write_local_settings(settings)
        if os.getenv("JARVIS_GOOGLE_CALENDAR") is None:
            self.google_calendar_enabled = bool(enabled)

    def save_calendar_write_settings(self, *, enabled: bool) -> None:
        settings, error = _load_local_settings(self.settings_path)
        if error or type(enabled) is not bool:
            raise ValueError('Configuração Calendar WRITE inválida; não ampliar acesso.')
        settings["google_calendar_write"] = bool(enabled)
        self._write_local_settings(settings)
        if os.getenv("JARVIS_GOOGLE_CALENDAR_WRITE") is None:
            self.google_calendar_write_enabled = bool(enabled)

    def save_voice_settings(
        self,
        *,
        provider: str | None = None,
        reference_audio: Path | None = None,
        clear_reference: bool = False,
        piper_pace: str | None = None,
    ) -> None:
        settings, error = _load_local_settings(self.settings_path)
        if error:
            raise ValueError(error)
        if self.settings_path.exists():
            settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
        if provider is not None:
            normalized = provider.casefold().strip()
            if normalized not in {"auto", "sapi", "piper", "chatterbox"}:
                raise ValueError("Provider de TTS inválido.")
            settings["tts_provider"] = normalized
        if clear_reference:
            settings.pop("tts_reference_audio", None)
        elif reference_audio is not None:
            settings["tts_reference_audio"] = str(reference_audio)
        if piper_pace is not None:
            normalized_pace = piper_pace.casefold().strip()
            if normalized_pace not in {"fast", "natural", "calm"}:
                raise ValueError("Ritmo do Piper inválido.")
            settings["tts_piper_pace"] = normalized_pace
        self._write_local_settings(settings)
        if provider is not None and os.getenv("JARVIS_TTS_PROVIDER") is None:
            self.tts_provider = provider.casefold().strip()
        if piper_pace is not None and os.getenv("JARVIS_TTS_PIPER_PACE") is None:
            self.tts_piper_pace = piper_pace.casefold().strip()
        if os.getenv("JARVIS_TTS_REFERENCE_AUDIO") is None:
            if clear_reference:
                self.tts_reference_audio_path = None
            elif reference_audio is not None:
                self.tts_reference_audio_path = reference_audio

    @property
    def tts_piper_length_scale(self) -> float:
        return {
            "fast": 1.00,
            "natural": 1.18,
            "calm": 1.30,
        }.get(self.tts_piper_pace, 1.18)

    def save_persona_settings(self, *, address_mode: str) -> None:
        normalized = address_mode.casefold().strip()
        if normalized not in {"senhor", "natural", "direto"}:
            raise ValueError("Modo de tratamento inválido.")
        settings, _error = _load_local_settings(self.settings_path)
        settings["assistant_address_mode"] = normalized
        self._write_local_settings(settings)
        if os.getenv("JARVIS_ASSISTANT_ADDRESS_MODE") is None:
            self.assistant_address_mode = normalized

    def save_conversation_preferences(self, *, user_display_name: str, tone: str,
                                      detail: str, opinions: bool, spoken: bool,
                                      voice: str, pace: str) -> None:
        """Literal administrative preferences, never memory or Tool authority."""
        from jarvis.tts.voices import installed_voices
        name = user_display_name.strip() if isinstance(user_display_name, str) else ""
        if (not 0 < len(name) <= 80 or any(ord(c) < 32 for c in name)
                or tone not in {"direto", "equilibrado", "descontraido"}
                or detail not in {"curtas", "detalhadas"}
                or type(opinions) is not bool or type(spoken) is not bool
                or pace not in {"fast", "natural", "calm"} or not isinstance(voice, str)):
            raise ValueError("Preferências inválidas.")
        chosen = next((v for v in installed_voices(self) if v.id == voice), None)
        if voice and chosen is None:
            raise ValueError("Voz não instalada ou incompatível.")
        settings, error = _load_local_settings(self.settings_path)
        if error:
            raise ValueError(error)
        # Retain unrelated settings, including keys owned by other integrations.
        if self.settings_path.exists():
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        settings.update(user_display_name=name, assistant_tone=tone,
                        assistant_detail=detail, assistant_opinions=opinions,
                        tts_enabled=spoken, tts_piper_pace=pace)
        if voice:
            settings["tts_piper_voice"] = voice
        self._write_local_settings(settings)
        self.user_display_name = name if os.getenv("JARVIS_USER_DISPLAY_NAME") is None else self.user_display_name
        self.assistant_tone, self.assistant_detail, self.assistant_opinions = tone, detail, opinions
        self.tts_enabled = spoken
        if os.getenv("JARVIS_TTS_PIPER_PACE") is None:
            self.tts_piper_pace = pace
        if chosen:
            self.tts_piper_model_path, self.tts_piper_config_path = chosen.model, chosen.config

    def save_identity_settings(
        self,
        *,
        user_display_name: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        settings, _error = _load_local_settings(self.settings_path)
        if user_display_name is not None:
            normalized_user = user_display_name.strip()
            if not normalized_user:
                raise ValueError("Nome do usuário não pode ficar vazio.")
            settings["user_display_name"] = normalized_user
        if agent_name is not None:
            normalized_agent = agent_name.strip()
            if not normalized_agent:
                raise ValueError("Nome do agente não pode ficar vazio.")
            settings["agent_name"] = normalized_agent
        self._write_local_settings(settings)
        if (
            user_display_name is not None
            and os.getenv("JARVIS_USER_DISPLAY_NAME") is None
        ):
            self.user_display_name = user_display_name.strip()
        if agent_name is not None and os.getenv("JARVIS_AGENT_NAME") is None:
            self.agent_name = agent_name.strip()

    def _write_local_settings(self, settings: dict[str, Any]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.settings_path.parent,
                prefix=f".{self.settings_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.settings_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        self.settings_error = None
