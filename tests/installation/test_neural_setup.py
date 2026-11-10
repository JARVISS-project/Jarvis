"""Exercise preparation boundaries without downloads, model imports or accounts."""
import json
from pathlib import Path
import subprocess

import pytest

from scripts import setup_neural_environment as setup


def platform(monkeypatch):
    monkeypatch.setattr(setup.sys, 'platform', 'win32')
    monkeypatch.setattr(setup.sys, 'version_info', (3, 13, 14))
    monkeypatch.setattr(setup.struct, 'calcsize', lambda _: 8)


@pytest.mark.parametrize('cuda', [False, True])
def test_new_environment_uses_hashed_dependencies_and_separate_empty_profile(tmp_path, monkeypatch, cuda):
    platform(monkeypatch)
    destination, profile = tmp_path/'software', tmp_path/'personal'
    calls = []
    monkeypatch.setenv('PYTHONPATH', 'unrelated-installation')
    monkeypatch.setenv('PYTHONHOME', 'unrelated-python')
    def create(path, **kwargs):
        assert kwargs == {'with_pip': True}
        path.mkdir()
    def run(arguments, **kwargs):
        assert kwargs['check'] and kwargs['cwd'] == setup.ROOT
        assert 'PYTHONPATH' not in kwargs['env'] and 'PYTHONHOME' not in kwargs['env']
        calls.append(arguments)
    setup.prepare(destination, cuda=cuda, profile_root=profile, create=create, run=run)
    assert len(calls) == 4
    assert '--download' in calls[0]
    assert '--require-hashes' in calls[1] and '--only-binary=:all:' in calls[1]
    assert calls[1][-1].endswith('neural-cuda.lock' if cuda else 'neural.lock')
    assert '--no-deps' in calls[2] and '--no-build-isolation' in calls[2]
    assert calls[3][-3:] == ['-m', 'pip', 'check']
    settings = json.loads((destination/'launch-config.json').read_text(encoding='utf-8'))
    assert settings['profile_root'] == str(profile.resolve())
    assert settings['device'] == ('cuda' if cuda else 'cpu')
    assert settings['model_directory'] is None
    assert not profile.exists(), 'Preparation must not populate a personal profile.'
    assert (destination/'launch_jarvis.pyw').read_bytes() == (setup.ROOT/'scripts/neural_desktop.pyw').read_bytes()
    assert '%~dp0Scripts\\pythonw.exe' in (destination/'Iniciar-JARVIS.cmd').read_text()


def test_existing_environment_and_profile_are_preserved(tmp_path, monkeypatch):
    platform(monkeypatch)
    (tmp_path/'keep.txt').write_text('existing')
    with pytest.raises(FileExistsError):
        setup.prepare(tmp_path, create=lambda *_a, **_k: pytest.fail('Must not create'))
    assert (tmp_path/'keep.txt').read_text() == 'existing'
    assert list(tmp_path.iterdir()) == [tmp_path/'keep.txt']


def test_refusals_happen_before_environment_creation(tmp_path, monkeypatch):
    platform(monkeypatch)
    create = lambda *_a, **_k: pytest.fail('Must refuse before creating anything')
    with pytest.raises(ValueError):
        setup.prepare(tmp_path/'env', profile_root=tmp_path/'env/personal', create=create)
    with pytest.raises(ValueError):
        setup.prepare(tmp_path/'env', model_directory=tmp_path/'missing', create=create)
    with pytest.raises(ValueError):
        setup.prepare(setup.ROOT/'unsafe-new-environment', create=create)
    assert not (tmp_path/'env').exists()


def test_failure_does_not_remove_partial_environment_or_write_launcher(tmp_path, monkeypatch):
    platform(monkeypatch)
    def create(path, **_):
        path.mkdir()
        (path/'keep').write_text('diagnostic')
    def fail(*_a, **_k):
        raise subprocess.CalledProcessError(1, 'simulated pip')
    destination=tmp_path/'env'
    with pytest.raises(subprocess.CalledProcessError):
        setup.prepare(destination, profile_root=tmp_path/'profile', create=create, run=fail)
    assert (destination/'keep').read_text() == 'diagnostic'
    assert not (destination/'Iniciar-JARVIS.cmd').exists()
    assert not (tmp_path/'profile').exists()


def test_local_models_are_checked_without_loading_or_modifying_them(tmp_path):
    for name in setup.MODEL_FILES:
        (tmp_path/name).write_bytes(b'not a model; inventory only')
    assert setup.validate_models(tmp_path) == tmp_path.resolve()
    with pytest.raises(ValueError):
        setup.validate_models('//server/models')
    assert all((tmp_path/name).read_bytes() == b'not a model; inventory only' for name in setup.MODEL_FILES)


def test_device_preference_is_process_local_and_explicit_config_wins(tmp_path, monkeypatch):
    from jarvis.config import JarvisConfig
    monkeypatch.setenv('JARVIS_TTS_DEVICE', 'cuda')
    assert JarvisConfig(settings_path=tmp_path/'absent.json').tts_device == 'cuda'
    assert JarvisConfig(settings_path=tmp_path/'absent.json', tts_device='cpu').tts_device == 'cpu'
    monkeypatch.delenv('JARVIS_TTS_DEVICE')
    assert JarvisConfig(settings_path=tmp_path/'absent.json').tts_device == 'auto'
