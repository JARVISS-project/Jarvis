from dataclasses import dataclass, field
import os
from pathlib import Path


def _obsidian_vault_from_environment() -> Path | None:
    value = os.getenv("JARVIS_OBSIDIAN_VAULT")
    return Path(value).expanduser() if value else None


def _environment_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on", "sim"}


@dataclass
class JarvisConfig:
    model_name: str = "qwen3:4b"
    conversation_max_messages: int = 20
    database_path: Path = Path("data/jarvis.db")
    audit_log_path: Path = Path("logs/audit.log")
    filesystem_base_directory: Path = field(default_factory=Path.cwd)
    obsidian_enabled: bool = field(
        default_factory=lambda: bool(os.getenv("JARVIS_OBSIDIAN_VAULT"))
    )
    obsidian_vault_path: Path | None = field(
        default_factory=_obsidian_vault_from_environment
    )
    filesystem_read_enabled: bool = True
    filesystem_write_enabled: bool = True
    obsidian_read_enabled: bool = True
    obsidian_write_enabled: bool = field(
        default_factory=lambda: _environment_flag(
            "JARVIS_OBSIDIAN_WRITE", False
        )
    )
    obsidian_search_max_results: int = 10
    obsidian_max_note_size_bytes: int = 1_000_000
    max_tool_iterations: int = 5
    ollama_keep_alive_normal: str = "5m"
    ollama_keep_alive_game_mode: str = "30s"
    llm_thinking_enabled: bool = False
    llm_debug_enabled: bool = field(
        default_factory=lambda: _environment_flag("JARVIS_LLM_DEBUG", False)
    )
    wake_word_enabled: bool = False
    wake_word_model: str = "hey_jarvis"
    wake_word_model_path: Path | None = None
    wake_word_threshold: float = 0.5
    wake_min_consecutive_detections: int = 2
    wake_word_cooldown_seconds: float = 1.5
    microphone_device_index: int | None = None
    stt_model: str = "small"
    stt_model_normal: str = "medium"
    stt_model_game_mode: str = "small"
    stt_language: str | None = "pt"
    stt_initial_prompt: str | None = (
        "Comandos curtos em português brasileiro."
    )
    stt_hotwords_enabled: bool = True
    stt_vad_enabled: bool = True
    stt_vad_min_silence_duration_ms: int = 500
    stt_vad_speech_pad_ms: int = 200
    stt_vad_threshold: float = 0.5
    stt_device_normal: str = "auto"
    stt_compute_type_normal: str = "auto"
    stt_device_game_mode: str = "cpu"
    stt_compute_type_game_mode: str = "int8"
    voice_silence_threshold: float = 500.0
    voice_silence_duration_seconds: float = 0.8
    voice_end_silence_seconds: float = 1.0
    voice_incomplete_turn_extension_seconds: float = 1.5
    voice_max_recording_seconds: float = 15.0
    voice_pre_speech_timeout_seconds: float = 5.0
    voice_minimum_speech_duration_seconds: float = 0.35
    voice_minimum_audio_energy: float = 700.0
    voice_noise_calibration_seconds: float = 0.3
    voice_noise_margin: float = 300.0
    voice_maximum_speech_threshold: float = 5000.0
    voice_pre_roll_seconds: float = 0.25
    voice_confirmation_enabled: bool = True
    voice_confirmation_timeout_seconds: float = 4.0
    voice_confirmation_min_speech_duration_seconds: float = 0.15
    voice_confirmation_keyboard_fallback: bool = False
    voice_repair_timeout_seconds: float = 8.0
    voice_acknowledgement_enabled: bool = True
    voice_debug_enabled: bool = field(
        default_factory=lambda: _environment_flag("JARVIS_VOICE_DEBUG", False)
    )
    voice_cancel_max_no_speech_probability: float = 0.35
    voice_cancel_min_average_log_probability: float = -0.6
    voice_cancel_min_speech_duration_seconds: float = 0.2
    conversation_followup_enabled: bool = True
    conversation_followup_timeout_seconds: float = 8.0
    conversation_question_timeout_seconds: float = 15.0
    conversation_followup_timeout_game_mode_seconds: float = 4.0
    pending_action_timeout_seconds: float = 30.0
    barge_in_enabled: bool = True
    barge_in_energy_margin: float = 500.0
    barge_in_min_speech_duration_seconds: float = 0.18
    barge_in_grace_period_seconds: float = 0.2
    tts_enabled: bool = True
    tts_enabled_game_mode: bool = True
    tts_rate: int = 180
    tts_volume: float = 1.0
    tts_voice_id: str | None = None
    tts_post_speech_cooldown_seconds: float = 0.75
