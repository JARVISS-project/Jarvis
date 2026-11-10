import json
from pathlib import Path
import wave

import numpy as np
import pytest

from jarvis.application.conversation_preferences import ConversationPreferences
from jarvis.tts.reference import import_reference
from jarvis.tts.factory import build_text_to_speech
from tests.application.test_conversation_preferences_23bm import config_with_voice, Sink


def sample(path, seconds=4, silent=False):
    with wave.open(str(path), 'wb') as file:
        file.setnchannels(2)
        file.setsampwidth(2)
        file.setframerate(16000)
        audio = np.zeros(seconds * 16000) if silent else np.sin(np.arange(seconds * 16000) * .1)*4000
        file.writeframes(np.repeat(audio.astype('<i2'), 2).tobytes())
    return path


def test_import_converts_only_selected_file_and_persists_provider(tmp_path, monkeypatch):
    config = config_with_voice(tmp_path, monkeypatch)
    source = sample(tmp_path / 'my voice.wav')
    original = source.read_bytes()
    service = ConversationPreferences(config)
    monkeypatch.setattr('jarvis.application.conversation_preferences.local_model_directory', lambda: tmp_path)
    try:
        state = service.import_voice(source=str(source))
        assert state['available'] and state['reference'] and state['provider'] == 'chatterbox'
        assert source.read_bytes() == original
        assert config.tts_reference_audio_path.parent == tmp_path / 'voices/references'
        with wave.open(str(config.tts_reference_audio_path)) as file:
            assert file.getnchannels() == 1 and file.getframerate() == 24000
        assert json.loads(config.settings_path.read_text())['tts_provider'] == 'chatterbox'
        assert service._backend is None  # No automatic playback/model loading.
    finally:
        service.close()


@pytest.mark.parametrize('seconds,silent', [(1, False), (31, False), (4, True)])
def test_rejected_audio_does_not_create_reference(tmp_path, seconds, silent):
    source = sample(tmp_path / 'reference.wav', seconds, silent)
    with pytest.raises(ValueError):
        import_reference(str(source), tmp_path / 'profile')
    assert not (tmp_path / 'profile').exists()


def test_unavailable_neural_component_never_claims_ready(tmp_path, monkeypatch):
    config = config_with_voice(tmp_path, monkeypatch)
    service = ConversationPreferences(config)
    monkeypatch.setattr('jarvis.application.conversation_preferences.find_spec', lambda name: None)
    service.configure_voice(provider='chatterbox')
    assert not service.inventory()['available']
    with pytest.raises(PermissionError):
        service.preview()
    assert service._backend is None


def test_personal_reference_does_not_fall_back_to_windows_voice(tmp_path, monkeypatch):
    config = config_with_voice(tmp_path, monkeypatch)
    config.tts_provider = 'chatterbox'
    config.tts_reference_audio_path = tmp_path / 'personal.wav'
    tts = build_text_to_speech(config)
    try:
        assert tts.fallback is None and not tts.is_model_loaded
    finally:
        tts.close()


def test_voice_change_invalidates_backend_and_busy_import_is_refused(tmp_path, monkeypatch):
    config = config_with_voice(tmp_path, monkeypatch)
    old, new = Sink(), Sink(blocked=True)
    service = ConversationPreferences(config, old, factory=lambda c: new)
    try:
        service.configure_voice(provider='sapi')
        service.speak_ui_async('Teste', preview=True)
        assert old.closed and new.calls == ['Teste']
        with pytest.raises(PermissionError):
            service.import_voice(source=str(tmp_path / 'missing.wav'))
        assert config.tts_reference_audio_path is None
    finally:
        service.close()
