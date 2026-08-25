from pathlib import Path

from jarvis.config import JarvisConfig


def test_config_defaults_match_current_application() -> None:
    config = JarvisConfig()

    assert config.model_name == "qwen3:4b"
    assert config.conversation_max_messages == 20
    assert config.database_path == Path("data/jarvis.db")
    assert config.audit_log_path == Path("logs/audit.log")
    assert config.filesystem_base_directory == Path.cwd()
    assert config.obsidian_enabled is False
    assert config.obsidian_vault_path is None
    assert config.filesystem_read_enabled is True
    assert config.filesystem_write_enabled is True
    assert config.obsidian_read_enabled is True
    assert config.obsidian_write_enabled is False
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
    assert config.stt_initial_prompt is not None
    assert config.stt_hotwords_enabled is True
    assert config.stt_vad_enabled is True
    assert config.stt_vad_min_silence_duration_ms == 500
    assert config.stt_vad_speech_pad_ms == 200
    assert config.stt_vad_threshold == 0.5
    assert config.stt_model_normal == "medium"
    assert config.stt_device_normal == "auto"
    assert config.stt_compute_type_normal == "auto"
    assert config.stt_model_game_mode == "small"
    assert config.stt_device_game_mode == "cpu"
    assert config.stt_compute_type_game_mode == "int8"
    assert config.voice_silence_threshold == 500.0
    assert config.voice_silence_duration_seconds == 0.8
    assert config.voice_max_recording_seconds == 15.0
    assert config.voice_pre_speech_timeout_seconds == 5.0
    assert config.voice_noise_calibration_seconds == 0.3
    assert config.voice_noise_margin == 300.0
    assert config.voice_maximum_speech_threshold == 5000.0
    assert config.voice_end_silence_seconds == 1.0
    assert config.voice_pre_roll_seconds == 0.25
    assert config.voice_confirmation_enabled is True
    assert config.voice_confirmation_timeout_seconds == 4.0
    assert config.voice_confirmation_min_speech_duration_seconds == 0.15
    assert config.voice_confirmation_keyboard_fallback is False
    assert config.voice_repair_timeout_seconds == 8.0
    assert config.voice_acknowledgement_enabled is True
    assert config.conversation_followup_enabled is True
    assert config.conversation_followup_timeout_seconds == 8.0
    assert config.conversation_question_timeout_seconds == 15.0
    assert config.conversation_followup_timeout_game_mode_seconds == 4.0
    assert config.barge_in_enabled is True
    assert config.barge_in_energy_margin == 500.0
    assert config.barge_in_min_speech_duration_seconds == 0.18
    assert config.barge_in_grace_period_seconds == 0.2
    assert config.tts_enabled is True
    assert config.tts_enabled_game_mode is True
    assert config.tts_rate == 180
    assert config.tts_volume == 1.0
    assert config.tts_voice_id is None
    assert config.tts_post_speech_cooldown_seconds == 0.75


def test_obsidian_config_comes_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT", "Vault/Teste")
    monkeypatch.setenv("JARVIS_OBSIDIAN_WRITE", "true")
    config = JarvisConfig()
    assert config.obsidian_enabled is True
    assert config.obsidian_vault_path == Path("Vault/Teste")
    assert config.obsidian_write_enabled is True
