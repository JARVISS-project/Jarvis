"""HTTP/OAuth/config boundaries; no live Google calls or consent."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from jarvis.config import JarvisConfig
from jarvis.integrations.google.drive_docs.client import DriveDocsClient, grant_digest
from jarvis.integrations.google.errors import (
    GoogleNetworkError, GooglePermissionError, GoogleRateLimitError,
    GoogleResourceNotFoundError, GoogleResponseError, GoogleTokenRevokedError,
)


class Auth:
    refreshes = 0
    def credentials(self):
        return SimpleNamespace(access_token='private-old-token', storage_material=lambda: self.material)
    def refresh_credentials(self):
        self.refreshes += 1
        return SimpleNamespace(access_token='private-new-token', storage_material=lambda: self.material)
    material = SimpleNamespace(client_id='client', refresh_token='private-refresh')


def binding():
    return grant_digest(Auth.material)


class Response(BytesIO):
    pass


def test_expired_token_one_refresh_get_only():
    auth = Auth()
    calls = []
    def opener(request, timeout):
        calls.append((request.get_method(), request.headers['Authorization'], timeout))
        if len(calls) == 1:
            raise HTTPError(request.full_url, 401, 'private', {}, BytesIO(b'private payload'))
        return Response(b'{"documentId":"file1"}')
    client = DriveDocsClient(auth, binding, opener)
    assert client.get('docs', '/documents/file1', {})['documentId'] == 'file1'
    assert auth.refreshes == 1 and len(calls) == 2
    assert all(method == 'GET' and timeout <= 10 for method, _, timeout in calls)
    assert calls[0][1] != calls[1][1]
    assert not any(hasattr(client, method) for method in ['post', 'patch', 'delete', 'download'])


@pytest.mark.parametrize('code,kind', [(403, GooglePermissionError),
    (404, GoogleResourceNotFoundError), (429, GoogleRateLimitError),
    (500, GoogleNetworkError), (401, GoogleTokenRevokedError)])
def test_http_errors_are_sanitized_without_blind_retry(code, kind):
    auth = Auth()
    calls = []
    def opener(request, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, code, 'title/token', {}, BytesIO(b'secret payload'))
    client = DriveDocsClient(auth, binding, opener)
    with pytest.raises(kind) as caught:
        client.get('drive', '/files/file1', {})
    assert 'secret' not in str(caught.value) and 'title' not in str(caught.value)
    assert len(calls) == (2 if code == 401 else 1)


@pytest.mark.parametrize('failure', [TimeoutError, OSError, URLError])
def test_network_timeouts_are_sanitized(failure):
    def opener(*args, **kwargs):
        raise failure('private data')
    with pytest.raises(GoogleNetworkError) as caught:
        DriveDocsClient(Auth(), binding, opener).get('drive', '/files', {})
    assert 'private data' not in str(caught.value)


@pytest.mark.parametrize('payload', [b'x'*1_048_577, b'[]', b'broken', b'\xff'],
                         ids=['oversize', 'array', 'invalid-json', 'invalid-utf8'])
def test_response_shape_encoding_and_size(payload):
    with pytest.raises(GoogleResponseError):
        DriveDocsClient(Auth(), binding, lambda *a, **k: Response(payload)).get('drive', '/files', {})


@pytest.mark.parametrize('service,path', [('drive', 'https://evil.invalid'),
    ('docs', '/documents/file1/evil'), ('drive', '/files/../secret'), ('evil', '/files')])
def test_endpoint_validation_before_http(service, path):
    calls = []
    with pytest.raises(PermissionError):
        DriveDocsClient(Auth(), lambda: 'a'*64, lambda *a, **k: calls.append(a)).get(service, path, {})
    assert calls == []


@pytest.mark.parametrize('value', [
    {'google_drive_docs_files': {'x': 'https://evil'}},
    {'google_drive_docs_files': {'UPPER': 'file1'}},
    {'google_drive_docs_folders': {'autorizados': 'folder'}},
    {'google_drive_docs_grant': 'bad'},
    {'google_drive_docs_files': {str(i): 'file1' for i in range(21)}},
])
def test_invalid_operator_configuration_fails_closed(tmp_path, value):
    path = tmp_path/'settings.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        JarvisConfig(settings_path=path)


def test_flags_are_independent_off_and_calendar_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv('JARVIS_GOOGLE_DRIVE_READ', raising=False)
    monkeypatch.delenv('JARVIS_GOOGLE_DOCS_READ', raising=False)
    path = tmp_path/'settings.json'
    path.write_text(json.dumps({'google_calendar': True,
        'google_calendar_authorized_calendars': {'principal': 'primary'},
        'google_drive_read': True, 'google_docs_read': False}))
    config = JarvisConfig(settings_path=path)
    assert config.google_calendar_enabled and config.google_drive_read_enabled and not config.google_docs_read_enabled
    default = JarvisConfig(settings_path=tmp_path/'none')
    assert not default.google_drive_read_enabled and not default.google_docs_read_enabled


def test_separate_factory_never_migrates_calendar_tokens(tmp_path, monkeypatch):
    from jarvis.integrations.google import factory
    from jarvis.integrations.google.scopes import DRIVE_DOCS_SELECTED_SCOPES, DRIVE_PICKER_SCOPES
    monkeypatch.setenv('JARVIS_GOOGLE_CREDENTIAL_DIR', str(tmp_path))
    legacy = tmp_path/'credentials_calendar_read.bin'
    legacy.write_bytes(b'opaque-calendar')
    seen = []
    monkeypatch.setattr(factory, 'build_google_auth_manager', lambda path, **kwargs: seen.append((path, kwargs)))
    factory.build_google_drive_docs_auth()
    assert seen[0][0].name == 'credentials_drive_docs_read.bin'
    assert seen[0][1]['scopes'] == DRIVE_PICKER_SCOPES
    assert set(DRIVE_PICKER_SCOPES) == {'https://www.googleapis.com/auth/drive.file'}
    assert seen[0][1]['allowed_scopes'] == DRIVE_DOCS_SELECTED_SCOPES
    assert set(DRIVE_DOCS_SELECTED_SCOPES) == {'openid', 'https://www.googleapis.com/auth/drive.file'}
    assert legacy.read_bytes() == b'opaque-calendar'
    assert not (tmp_path/'credentials_drive_docs_read.bin').exists()


def test_account_changed_before_transport_has_zero_calls():
    calls = []
    with pytest.raises(PermissionError):
        DriveDocsClient(Auth(), lambda: 'b'*64, lambda *a, **k: calls.append(a)).get('drive', '/files', {})
    assert calls == []


def test_local_grant_binding_never_refreshes_or_exposes_material(monkeypatch, tmp_path):
    from jarvis.integrations.google import factory
    from jarvis.integrations.google.models import StoredCredentialMaterial
    from jarvis.integrations.google.scopes import DRIVE_DOCS_SELECTED_SCOPES
    material = StoredCredentialMaterial('private-refresh', 'https://oauth2.googleapis.com/token',
                                       'client', scopes=DRIVE_DOCS_SELECTED_SCOPES)
    loads = []
    class Store:
        def __init__(self, path, protector):
            assert path == tmp_path/'read.bin'
        def load(self):
            loads.append(True)
            return material
    monkeypatch.setattr(factory, 'ProtectedFileCredentialStore', Store)
    binding_value = factory.drive_docs_grant_binding(tmp_path/'read.bin')
    assert len(binding_value) == 64 and binding_value == grant_digest(material) and loads == [True]


@pytest.mark.parametrize('command', ['connect', 'status', 'disconnect'])
def test_cli_uses_only_separate_minimal_factory(monkeypatch, tmp_path, command, capsys):
    from jarvis.runtime import cli
    from jarvis.integrations.google import factory
    from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
    calls = []
    class Manager:
        def connect(self, path, timeout_seconds):
            calls.append(('connect', path))
            return ConnectionStatus(ConnectionState.CONNECTED)
        def status(self):
            calls.append(('status',))
            return ConnectionStatus(ConnectionState.DISCONNECTED)
        def disconnect(self):
            calls.append(('disconnect',))
            return ConnectionStatus(ConnectionState.DISCONNECTED)
    monkeypatch.setattr(factory, 'build_google_drive_docs_auth', lambda: Manager())
    from jarvis.integrations.google.drive_docs import picker
    monkeypatch.setattr(picker, 'GoogleDocumentPicker', lambda _config: SimpleNamespace(
        disconnect_local=lambda: calls.append(('disconnect',))))
    monkeypatch.setattr(factory, 'drive_docs_grant_binding', lambda: 'a'*64)
    monkeypatch.setattr(factory, 'build_google_calendar_minimal_read_auth', lambda: pytest.fail('Calendar must remain untouched'))
    arguments = ['--google-drive-docs', command, '--google-client-config', str(tmp_path/'desktop.json')]
    assert cli._handle_google_command(cli.argument_parser().parse_args(arguments)) == 0
    assert calls[0][0] == command and 'Google Drive/Docs' in capsys.readouterr().out


def test_connected_cli_requires_valid_local_binding(monkeypatch, capsys):
    from jarvis.runtime import cli
    from jarvis.integrations.google import factory
    from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
    monkeypatch.setattr(factory, 'build_google_drive_docs_auth', lambda: SimpleNamespace(
        status=lambda: ConnectionStatus(ConnectionState.CONNECTED)))
    def missing():
        raise PermissionError('private details must not be printed')
    monkeypatch.setattr(factory, 'drive_docs_grant_binding', missing)
    assert cli._handle_google_command(cli.argument_parser().parse_args(['--google-drive-docs', 'status'])) == 1
    assert 'private details' not in capsys.readouterr().out
