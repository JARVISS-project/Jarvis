from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


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
        "assistant_address_mode": str,
        "desktop_read": bool,
        "desktop_control": bool,
        "vision": bool,
        "browser": bool,
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
    database_path: Path = Path("data/jarvis.db")
    audit_log_path: Path = Path("logs/audit.log")
    filesystem_base_directory: Path = field(default_factory=Path.cwd)
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
    browser_allowed_domains: list[str] = field(default_factory=list)
    browser_blocked_domains: list[str] = field(default_factory=list)
    browser_max_tabs: int = 10
    obsidian_search_max_results: int = 10
    obsidian_max_note_size_bytes: int = 1_000_000
    max_tool_iterations: int = 5
    ollama_keep_alive_normal: str = "5m"
    ollama_keep_alive_game_mode: str = "30s"
    llm_thinking_enabled: bool = False
    llm_warm_up_on_voice_start: bool = True
    llm_debug_enabled: bool = field(
        default_factory=lambda: _environment_flag("JARVIS_LLM_DEBUG", False)
    )
    wake_word_enabled: bool = False
    # A experiência usa "Jarvis" como chamada. O modelo oficial disponível no
    # openWakeWord continua sendo ``hey_jarvis`` e também pode reconhecer
    # "Jarvis" isoladamente, embora com maior chance de rejeição.
    wake_word_phrase: str = "Jarvis"
    wake_word_model: str = "hey_jarvis"
    wake_word_model_path: Path | None = None
    wake_word_threshold: float = 0.5
    wake_min_consecutive_detections: int = 2
    wake_word_cooldown_seconds: float = 1.5
    ambient_ui_endpoint_path: Path = Path("data/runtime/ambient-ui.json")
    microphone_device_index: int | None = None
    stt_model: str = "small"
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
    # Wake acknowledgement confirms presence immediately after "Jarvis".
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
    tts_piper_model_path: Path = Path("data/voices/razo/pt-BR-razo-medium.onnx")
    tts_piper_config_path: Path = Path("data/voices/razo/pt-BR-razo-medium.onnx.json")
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

    def save_voice_settings(
        self,
        *,
        provider: str | None = None,
        reference_audio: Path | None = None,
        clear_reference: bool = False,
        piper_pace: str | None = None,
    ) -> None:
        settings, _error = _load_local_settings(self.settings_path)
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

    def _write_local_settings(self, settings: dict[str, Any]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.settings_error = None
