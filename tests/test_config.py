from pathlib import Path

from jarvis.config import JarvisConfig


def test_config_defaults_match_current_application() -> None:
    config = JarvisConfig()

    assert config.model_name == "qwen3:4b"
    assert config.conversation_max_messages == 20
    assert config.database_path == Path("data/jarvis.db")
    assert config.audit_log_path == Path("logs/audit.log")
    assert config.filesystem_base_directory == Path.cwd()
    assert config.max_tool_iterations == 5
    assert config.ollama_keep_alive_normal == "5m"
    assert config.ollama_keep_alive_game_mode == "30s"
    assert config.llm_thinking_enabled is False
    assert config.wake_word_enabled is False
    assert config.wake_word_model == "hey_jarvis"
    assert config.wake_word_model_path is None
    assert config.wake_word_threshold == 0.5
    assert config.microphone_device_index is None
    assert config.stt_model == "small"
    assert config.stt_device_normal == "cpu"
    assert config.stt_compute_type_normal == "int8"
    assert config.stt_device_game_mode == "cpu"
    assert config.stt_compute_type_game_mode == "int8"
    assert config.voice_silence_threshold == 500.0
    assert config.voice_silence_duration_seconds == 0.8
    assert config.voice_max_recording_seconds == 15.0
    assert config.voice_pre_speech_timeout_seconds == 5.0
    assert config.tts_enabled is True
    assert config.tts_enabled_game_mode is True
    assert config.tts_rate == 180
    assert config.tts_volume == 1.0
    assert config.tts_voice_id is None
    assert config.tts_post_speech_cooldown_seconds == 0.75
