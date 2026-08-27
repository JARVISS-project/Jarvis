from __future__ import annotations

from importlib.util import find_spec

from jarvis.config import JarvisConfig
from jarvis.tts.base import TextToSpeech
from jarvis.tts.chatterbox import ChatterboxTTS
from jarvis.tts.piper import PiperTTS
from jarvis.tts.windows_sapi import WindowsSapiTTS


def build_text_to_speech(config: JarvisConfig) -> TextToSpeech | None:
    if not config.tts_enabled:
        return None

    sapi = WindowsSapiTTS(
        rate=config.tts_rate,
        volume=config.tts_volume,
        voice_id=config.tts_voice_id,
    )
    provider = config.tts_provider.casefold().strip()
    if provider == "sapi":
        return sapi

    piper_installed = (
        find_spec("piper") is not None
        and find_spec("sounddevice") is not None
    )
    piper_ready = (
        config.tts_piper_model_path.is_file()
        and config.tts_piper_config_path.is_file()
    )
    if provider == "piper" or (
        provider == "auto" and piper_installed and piper_ready
    ):
        return PiperTTS(
            model_path=config.tts_piper_model_path,
            config_path=config.tts_piper_config_path,
            fallback=sapi,
            tail_silence_seconds=config.tts_piper_tail_silence_seconds,
            length_scale=config.tts_piper_length_scale,
            sentence_silence_seconds=config.tts_piper_sentence_silence_seconds,
        )

    neural_installed = (
        find_spec("chatterbox") is not None
        and find_spec("sounddevice") is not None
    )
    if provider == "auto" and not neural_installed:
        return sapi

    fallback = sapi if config.tts_chatterbox_fallback_to_sapi else None
    return ChatterboxTTS(
        device=config.tts_device,
        language_id=config.tts_language_id,
        reference_audio_path=config.tts_reference_audio_path,
        t3_model=config.tts_chatterbox_t3_model,
        exaggeration=config.tts_chatterbox_exaggeration,
        cfg_weight=config.tts_chatterbox_cfg_weight,
        fallback=fallback,
    )
