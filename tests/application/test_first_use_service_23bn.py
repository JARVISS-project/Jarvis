import json
from datetime import datetime, timedelta, timezone

import pytest

from jarvis.application.first_use import FirstUseService, STEPS
from jarvis.security.capabilities import Capability
from tests.integration.test_administration_23bh import build, change


@pytest.fixture
def system(tmp_path):
    harness, service = build(tmp_path)
    yield harness, service
    harness.close_confirmation_sessions()


def test_progress_first_use_skip_resume_back_complete_and_restart(system):
    h, service = system
    first = service.first_use
    assert first.progress()['auto_show'] and not first.path.exists()
    assert first.update('next')['step'] == 'hardware'
    assert first.update('skip')['dismissed']
    restarted = FirstUseService(service)
    assert not restarted.progress()['auto_show']
    assert restarted.update('resume')['step'] == 'hardware'
    assert restarted.update('back')['step'] == 'welcome'
    for _ in STEPS:
        restarted.update('next')
    assert restarted.progress()['finished']
    assert not FirstUseService(service).progress()['auto_show']
    assert restarted.update('resume')['completed'] == [s[0] for s in STEPS]


def test_migration_preserves_known_progress_and_invalid_is_not_overwritten(system):
    h, service = system
    first = service.first_use
    first.path.write_text(json.dumps(dict(version=0, step='retired', completed=['welcome', 'retired'], dismissed=True)))
    assert first.progress()['completed'] == ['welcome']
    assert first.progress()['step'] == 'hardware'
    assert first.update('resume')['version'] == 1
    first.path.write_text('{invalid')
    assert not first.progress()['valid'] and not first.progress()['auto_show']
    with pytest.raises(ValueError):
        first.update('next')
    assert first.path.read_text() == '{invalid'


def test_help_uses_registered_tools_effective_permissions_and_capability(system, monkeypatch):
    h, service = system
    first = service.first_use
    def forbidden(*args, **kwargs):
        pytest.fail('Help may not execute effects or providers')
    monkeypatch.setattr(h.tool_executor, 'execute', forbidden)
    monkeypatch.setattr(h.llm, 'generate', forbidden)
    def docs():
        return next(r for r in first.inventory()['examples'] if r['category'] == 'Documentos locais')
    assert docs()['available']
    change(service, 'tool:read_file', 'block')
    assert not docs()['available']
    change(service, 'tool:read_file', 'allow')
    service.capability_policy._permissions[Capability.FILESYSTEM_READ] = False
    assert not docs()['available']
    data = first.inventory()
    assert len(data['examples']) == 10
    assert not next(r for r in data['examples'] if r['category'] == 'Agenda')['available']
    assert not next(r for r in data['examples'] if r['category'] == 'Briefing')['available']
    assert not service.first_use.path.exists()
    assert not h.memory_store.get_all()


def test_profile_requires_preview_identity_revision_and_single_use(system):
    h, service = system
    first = service.first_use
    with pytest.raises(PermissionError):
        first.prepare_profile('large-v3', 'owner')
    first.latest = {'profiles': [{'profile': 'medium'}, {'profile': 'large-v3'}]}
    service.config.settings_path.write_text(json.dumps({'custom_integration': {'preserve': True}, 'tts_piper_pace': 'natural'}))
    before = service.config.settings_path.read_bytes()
    preview = first.prepare_profile('large-v3', 'owner')
    assert service.config.settings_path.read_bytes() == before
    with pytest.raises(PermissionError):
        first.apply_profile(preview['token'], 'other')
    preview = first.prepare_profile('large-v3', 'owner')
    changed = json.loads(before)
    changed['user_display_name'] = 'Pessoa'
    service.config.settings_path.write_text(json.dumps(changed))
    with pytest.raises(PermissionError):
        first.apply_profile(preview['token'], 'owner')
    preview = first.prepare_profile('large-v3', 'owner')
    assert first.apply_profile(preview['token'], 'owner')['rollback_available']
    assert service.config.stt_profile == 'large-v3'
    stored = json.loads(service.config.settings_path.read_text())
    assert stored['custom_integration'] == {'preserve': True} and stored['user_display_name'] == 'Pessoa'
    assert service.config.google_calendar_write_enabled is False
    with pytest.raises(PermissionError):
        first.apply_profile(preview['token'], 'owner')
    with pytest.raises(PermissionError):
        first.rollback_profile('other')
    assert first.rollback_profile('owner')['profile'] == 'medium'
    assert json.loads(service.config.settings_path.read_text())['custom_integration'] == {'preserve': True}


def test_expired_preview_environment_override_and_bad_schema(system, monkeypatch):
    h, service = system
    first = service.first_use
    first.latest = {'profiles': [{'profile': 'medium'}]}
    p = first.prepare_profile('medium')
    row = first._prepared[p['token']]
    first._prepared[p['token']] = (*row[:-1], datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(PermissionError):
        first.apply_profile(p['token'])
    monkeypatch.setenv('JARVIS_STT_PROFILE', 'medium')
    with pytest.raises(PermissionError):
        first.prepare_profile('medium')
    for method, args in [('help.diagnose', {'url': 'https://example.com'}), ('help.progress', {'action': 'grant'}), ('help.profile_apply', {'token': '', 'profile': 'large-v3'})]:
        with pytest.raises((ValueError, PermissionError)):
            service.dispatch(method, args)
    assert not service.config.settings_path.exists()


def test_tutorial_and_diagnostic_do_not_initialize_effects_or_transmit(system, tmp_path, monkeypatch):
    from jarvis.integrations.google.auth import GoogleAuthManager
    from jarvis.tts.piper import PiperTTS
    from jarvis.speech.faster_whisper import FasterWhisperSTT
    from jarvis.runtime import hardware
    from jarvis.speech.gpu_budget import GpuBudgetManager, GpuMemory
    h, service = system
    def forbidden(*args, **kwargs):
        pytest.fail('Unexpected effect, OAuth, model loading, audio or transmission')
    monkeypatch.setattr(GoogleAuthManager, 'connect', forbidden)
    monkeypatch.setattr(PiperTTS, '__init__', forbidden)
    monkeypatch.setattr(FasterWhisperSTT, '__init__', forbidden)
    monkeypatch.setattr(h.tool_executor, 'execute', forbidden)
    monkeypatch.setattr(h.llm, 'generate', forbidden)
    monkeypatch.setattr(hardware.urllib.request, 'build_opener', forbidden)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    service.first_use.probe = lambda cfg: hardware.hardware_report(cfg,
        memory_probe=lambda: {}, model_probe=lambda: {'status': 'unavailable', 'models': []},
        gpu_probe=lambda: {'status': 'unavailable'}, gpu_budget=GpuBudgetManager(lambda: GpuMemory(4096, 0, 4096)))
    service.dispatch('help.list', {})
    service.dispatch('help.diagnose', {})
    for _ in STEPS:
        service.dispatch('help.progress', {'action': 'next'})
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file() and p.name != 'onboarding.json'}
    assert after == before
    assert not service.permissions.path.exists()
    assert not h.memory_store.get_all()
