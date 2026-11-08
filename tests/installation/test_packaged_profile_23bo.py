import json
from pathlib import Path
import sys
import os

import pytest

from jarvis.runtime.packaged import configure_profile
from jarvis.security.permissions import PermissionStore


@pytest.fixture
def isolated(monkeypatch):
    old = Path.cwd()
    for key in ('JARVIS_GOOGLE_CREDENTIAL_DIR', 'JARVIS_PACKAGED_PROFILE', 'HF_HOME',
                'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'TEMP', 'TMP'):
        monkeypatch.setenv(key, '')
    yield
    os.chdir(old)


def test_fresh_profile_outside_program_blocks_capture_and_files(tmp_path, isolated, monkeypatch):
    profile = tmp_path / 'profile'
    configure_profile(profile)
    assert Path.cwd() == profile
    settings = json.loads((profile / 'data/settings.json').read_text())
    assert not settings['google_calendar_write_enabled'] and not settings['tts_enabled']
    permissions = PermissionStore(profile / 'data/permissions.json', (profile / 'documents',))
    assert permissions.decision('microphone.capture').mode == 'block'
    for action in ('read', 'write', 'delete'):
        assert permissions.path_decision(profile / 'documents', action) == 'block'
    import os
    assert os.environ['JARVIS_GOOGLE_CREDENTIAL_DIR'] == str(profile / 'google')
    assert os.environ['HF_HUB_OFFLINE'] == '1'
    assert (profile / 'models').is_dir() and (profile / 'tmp').is_dir()


def test_reinstall_preserves_settings_memory_credentials_permissions_and_progress(tmp_path, isolated):
    profile = tmp_path / 'profile'
    configure_profile(profile)
    for relative in ('data/settings.json', 'data/jarvis.db', 'data/onboarding.json', 'google/accounts.bin'):
        (profile / relative).write_bytes(b'preserve existing bytes')
    before = {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    configure_profile(profile)
    assert before == {p: p.read_bytes() for p in profile.rglob('*') if p.is_file()}


def test_program_directory_rejected_before_writes(tmp_path, isolated, monkeypatch):
    executable = tmp_path / 'program' / 'JARVIS.exe'
    monkeypatch.setattr(sys, 'executable', str(executable))
    with pytest.raises(ValueError):
        configure_profile(executable.parent / 'profile')
    assert not executable.parent.exists()


def test_frozen_ui_child_uses_executable_and_absolute_endpoint(tmp_path, monkeypatch):
    from jarvis.runtime import ambient_ui
    calls = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(ambient_ui.subprocess, 'Popen', lambda args, **kwargs: calls.append(args))
    ambient_ui.launch_ambient_ui(tmp_path / 'endpoint.json')
    assert calls[0][0] == sys.executable and calls[0][1] == '--ui-child'
    assert '-m' not in calls[0] and Path(calls[0][-1]).is_absolute()


def test_installer_preserves_data_and_has_no_automatic_execution():
    root = Path(__file__).resolve().parents[2]
    text = (root / 'packaging/windows/installer.iss').read_text(encoding='utf-8')
    assert 'PrivilegesRequired=lowest' in text
    assert '[Run]' not in text and '[UninstallDelete]' not in text
    assert 'DelTree' not in text and '[Registry]' not in text
    assert 'TestInstance' in text
    entry = (root / 'packaging/windows/entry.py').read_text(encoding='utf-8')
    assert entry.index('freeze_support()') < entry.index('from jarvis.runtime.packaged import main')


def test_frozen_models_separate_and_optional_worker_never_spawns(tmp_path, isolated, monkeypatch):
    from jarvis.config import JarvisConfig
    from jarvis.speech.ptbr_fallback import PtBrFallbackClient
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    profile = configure_profile(tmp_path / 'profile')
    cfg = JarvisConfig()
    assert cfg.filesystem_base_directory == profile / 'documents'
    assert cfg.tts_piper_model_path.parts[0] == 'models'
    assert not cfg.stt_ptbr_fallback_enabled
    def forbidden(*args, **kwargs):
        pytest.fail('Optional worker must not launch a second UI')
    from jarvis.speech import ptbr_fallback
    monkeypatch.setattr(ptbr_fallback.subprocess, 'Popen', forbidden)
    fallback = PtBrFallbackClient()
    assert not fallback._ensure_worker()
    fallback.close()
