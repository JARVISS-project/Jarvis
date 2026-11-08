import json
import os
from pathlib import Path

from jarvis.config import JarvisConfig
from jarvis.runtime.packaged import configure_profile


def test_new_public_profile_starts_without_connected_notes_or_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ('JARVIS_OBSIDIAN_VAULT', 'JARVIS_USER_DISPLAY_NAME',
                'JARVIS_ASSISTANT_ADDRESS_MODE'):
        monkeypatch.delenv(key, raising=False)
    for key in ('JARVIS_GOOGLE_CREDENTIAL_DIR', 'JARVIS_PACKAGED_PROFILE',
                'HF_HOME', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'TEMP', 'TMP'):
        monkeypatch.setenv(key, os.environ.get(key, ''))
    profile = configure_profile(tmp_path / 'new-user')
    settings = json.loads((profile / 'data/settings.json').read_text())
    assert settings['obsidian_vault'] == ''
    assert settings['user_display_name'] == ''
    assert settings['assistant_address_mode'] == 'natural'
    assert settings['tts_enabled'] is False
    config = JarvisConfig()
    assert config.obsidian_vault_path is None
    assert config.obsidian_enabled is False
    assert not list((profile / 'documents').iterdir())
    assert not list((profile / 'google').iterdir())
    assert not (profile / 'data/jarvis.db').exists()


def test_existing_user_preferences_are_preserved_when_profile_reopens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ('JARVIS_GOOGLE_CREDENTIAL_DIR', 'JARVIS_PACKAGED_PROFILE',
                'HF_HOME', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'TEMP', 'TMP'):
        monkeypatch.setenv(key, os.environ.get(key, ''))
    profile = tmp_path / 'existing-user'
    (profile / 'data').mkdir(parents=True)
    settings = b'{"user_display_name":"Pessoa","assistant_address_mode":"direto"}'
    (profile / 'data/settings.json').write_bytes(settings)
    configure_profile(profile)
    assert (profile / 'data/settings.json').read_bytes() == settings


def test_public_source_excludes_private_operations_and_history():
    root = Path(__file__).resolve().parents[2]
    assert not list((root / 'src/jarvis').glob('orca*.py'))
    assert not (root / 'docs/history').exists()
    assert not (root / 'config/orca_tasks.json').exists()
    assert not (root / 'opencode.json').exists()
