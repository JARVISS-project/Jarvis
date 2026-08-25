from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JarvisConfig:
    model_name: str = "qwen3:4b"
    conversation_max_messages: int = 20
    database_path: Path = Path("data/jarvis.db")
    audit_log_path: Path = Path("logs/audit.log")
    filesystem_base_directory: Path = field(default_factory=Path.cwd)
    max_tool_iterations: int = 5
    ollama_keep_alive_normal: str = "5m"
    ollama_keep_alive_game_mode: str = "30s"
    llm_thinking_enabled: bool = False
    wake_word_enabled: bool = False
    wake_word_model: str = "hey_jarvis"
    wake_word_model_path: Path | None = None
    wake_word_threshold: float = 0.5
    wake_word_cooldown_seconds: float = 1.5
    microphone_device_index: int | None = None
    stt_model: str = "small"
    stt_language: str | None = "pt"
    stt_device_normal: str = "cpu"
    stt_compute_type_normal: str = "int8"
    stt_device_game_mode: str = "cpu"
    stt_compute_type_game_mode: str = "int8"
    voice_silence_threshold: float = 500.0
    voice_silence_duration_seconds: float = 0.8
    voice_max_recording_seconds: float = 15.0
    voice_pre_speech_timeout_seconds: float = 5.0
    voice_minimum_speech_duration_seconds: float = 0.35
    voice_minimum_audio_energy: float = 700.0
    tts_enabled: bool = True
    tts_enabled_game_mode: bool = True
    tts_rate: int = 180
    tts_volume: float = 1.0
    tts_voice_id: str | None = None
    tts_post_speech_cooldown_seconds: float = 0.75
