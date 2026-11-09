import json
import pytest

from jarvis.integrations.google import public_login
from jarvis.integrations.google.errors import GoogleClientConfigurationError


def client_file(tmp_path):
    config = {'installed': {
        'client_id': '123-synthetic.apps.googleusercontent.com',
        'project_id': public_login.PUBLIC_PROJECT,
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_secret': 'GOCSPX-' + 'synthetic-registration-only',
        'redirect_uris': ['http://localhost'],
    }}
    path = tmp_path / 'public-login.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path, config


def test_local_inventory_exposes_only_testing_availability(tmp_path, monkeypatch):
    path, config = client_file(tmp_path)
    monkeypatch.setattr(public_login, 'bundled_path', lambda: path)
    status = public_login.public_login_status()
    assert status == {'available': True, 'mode': 'testing', 'operations': ['calendar_read']}
    assert config['installed']['client_secret'] not in json.dumps(status)
    assert public_login.public_client_path('calendar_read') == path
    for operation in ('calendar_write', 'drive_docs_read', 'authentication', '../calendar_read'):
        with pytest.raises(GoogleClientConfigurationError):
            public_login.public_client_path(operation)


@pytest.mark.parametrize('mutation', ['personal-project', 'user-token', 'web', 'endpoint',
                                     'endpoint-type', 'redirect', 'certificate', 'secret-type'])
def test_rejects_profiles_other_projects_and_endpoints(tmp_path, monkeypatch, mutation):
    path, config = client_file(tmp_path)
    installed = config['installed']
    if mutation == 'personal-project':
        installed['project_id'] = 'personal-project'
    elif mutation == 'user-token':
        installed['refresh_token'] = 'synthetic-user-token'
    elif mutation == 'web':
        config = {'web': installed}
    elif mutation == 'endpoint':
        installed['token_uri'] = 'https://attacker.invalid/token'
    elif mutation == 'endpoint-type':
        installed['auth_uri'] = []
    elif mutation == 'redirect':
        installed['redirect_uris'] = ['https://attacker.invalid/callback']
    elif mutation == 'certificate':
        installed['auth_provider_x509_cert_url'] = 'https://attacker.invalid/certs'
    else:
        installed['client_secret'] = {'refresh_token': 'synthetic-user-token'}
    path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setattr(public_login, 'bundled_path', lambda: path)
    assert not public_login.public_login_status()['available']
    with pytest.raises(GoogleClientConfigurationError) as captured:
        public_login.public_client_path('calendar_read')
    assert 'synthetic-user-token' not in str(captured.value)


def test_never_discovers_ambient_client_or_working_directory(tmp_path, monkeypatch):
    path, _ = client_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('JARVIS_GOOGLE_CLIENT_CONFIG', str(path))
    monkeypatch.setattr(public_login, 'bundled_path', lambda: tmp_path / 'missing-package-resource.json')
    assert not public_login.public_login_status()['available']


def test_account_bundled_connection_uses_existing_gates_and_rejects_write(tmp_path, monkeypatch):
    from tests.integrations.google.test_accounts_23bk import make, row
    service, _, managers = make(tmp_path / 'accounts')
    path, _ = client_file(tmp_path)
    monkeypatch.setattr(public_login, 'bundled_path', lambda: path)
    data = service.inventory()
    reader = row(service, 'calendar_read')
    calls = []
    manager = service.auth_factory('calendar_read', service.directory / 'credentials_calendar_read.bin')
    original_connect = manager.connect
    def connect(client_path, **kwargs):
        calls.append(client_path)
        return original_connect(client_path, **kwargs)
    monkeypatch.setattr(manager, 'connect', connect)
    with pytest.raises(PermissionError):
        service.connect(reader['id'], 'stale-revision', public_login.PUBLIC_CLIENT)
    assert not calls
    result = service.connect(reader['id'], data['revision'], public_login.PUBLIC_CLIENT)
    assert calls == [path]
    assert not result['write_enabled']
    writer = row(service, 'calendar_write')
    before = service.path.read_bytes()
    with pytest.raises(GoogleClientConfigurationError):
        service.connect(writer['id'], result['revision'], public_login.PUBLIC_CLIENT)
    assert service.path.read_bytes() == before
