from jarvis.config import JarvisConfig
from jarvis.tts.chatterbox import ChatterboxTTS
from jarvis.tts.factory import build_text_to_speech
from jarvis.tts.piper import PiperTTS
from jarvis.tts.windows_sapi import WindowsSapiTTS


def test_auto_provider_uses_sapi_when_neural_extra_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "jarvis.tts.factory.find_spec",
        lambda _name: None,
    )

    tts = build_text_to_speech(JarvisConfig(tts_provider="auto"))

    assert isinstance(tts, WindowsSapiTTS)
    assert tts.is_engine_initialized is False
    tts.close()


def test_explicit_chatterbox_provider_is_lazy_and_keeps_sapi_fallback() -> None:
    tts = build_text_to_speech(JarvisConfig(tts_provider="chatterbox"))

    assert isinstance(tts, ChatterboxTTS)
    assert tts.is_model_loaded is False
    assert isinstance(tts.fallback, WindowsSapiTTS)
    tts.close()


def test_auto_provider_prefers_ready_piper_voice(tmp_path, monkeypatch) -> None:
    model = tmp_path / "razo.onnx"
    config_path = tmp_path / "razo.onnx.json"
    model.write_bytes(b"model")
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "jarvis.tts.factory.find_spec",
        lambda name: object() if name in {"piper", "sounddevice", "chatterbox"} else None,
    )

    tts = build_text_to_speech(JarvisConfig(
        tts_provider="auto",
        tts_piper_model_path=model,
        tts_piper_config_path=config_path,
    ))

    assert isinstance(tts, PiperTTS)
    assert tts.is_model_loaded is False
    tts.close()


def test_explicit_piper_provider_is_lazy(tmp_path) -> None:
    tts = build_text_to_speech(JarvisConfig(
        tts_provider="piper",
        tts_piper_model_path=tmp_path / "missing.onnx",
        tts_piper_config_path=tmp_path / "missing.onnx.json",
    ))

    assert isinstance(tts, PiperTTS)
    assert tts.is_model_loaded is False
    tts.close()
