import subprocess
from scripts import build_windows_23bo as build


def test_build_requires_explicit_identity_argument_and_clears_ambient_value(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv('JARVIS_BUILD_GOOGLE_LOGIN', 'personal-profile/credentials.json')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: calls.append(kwargs['env']))
    build.run(['synthetic-command'])
    assert 'JARVIS_BUILD_GOOGLE_LOGIN' not in calls[-1]
    explicit = tmp_path / 'dedicated-desktop.json'
    build.run(['synthetic-command'], google_login=explicit)
    assert calls[-1]['JARVIS_BUILD_GOOGLE_LOGIN'] == str(explicit)
    build.run(['synthetic-command'])
    assert 'JARVIS_BUILD_GOOGLE_LOGIN' not in calls[-1]
