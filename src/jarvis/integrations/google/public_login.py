"""Optional application identity; never discovers or copies a user's credentials."""
from __future__ import annotations

from pathlib import Path
import re

from jarvis.integrations.google.client import load_client_config
from jarvis.integrations.google.errors import GoogleClientConfigurationError


PUBLIC_CLIENT = '@jarvis-public-google'
PUBLIC_PROJECT = 'jarvis-public-514123'
PUBLIC_OPERATIONS = ('calendar_read',)


def bundled_path() -> Path:
    # Fixed package resource, independent of cwd, user profile and environment.
    return Path(__file__).with_name('public-login.json')


def validate_public_client(path: Path) -> dict:
    """Allow only the dedicated Desktop application's registration metadata."""
    try:
        value = load_client_config(path)
    except (KeyError, TypeError, ValueError):
        raise GoogleClientConfigurationError('Identidade pública Google inválida; login não iniciado.') from None
    client = value['installed']
    allowed = {'client_id', 'project_id', 'auth_uri', 'token_uri', 'client_secret',
               'auth_provider_x509_cert_url', 'redirect_uris'}
    if (set(client) - allowed
            or client.get('project_id') != PUBLIC_PROJECT
            or not re.fullmatch(r'[0-9]+-[a-z0-9-]+\.apps\.googleusercontent\.com', client['client_id'])
            or not isinstance(client.get('client_secret'), str)
            or not re.fullmatch(r'GOCSPX-[A-Za-z0-9_-]{16,128}', client['client_secret'])
            or client.get('redirect_uris') != ['http://localhost']
            or client.get('auth_provider_x509_cert_url', 'https://www.googleapis.com/oauth2/v1/certs')
                != 'https://www.googleapis.com/oauth2/v1/certs'):
        raise GoogleClientConfigurationError('Identidade pública Google inválida; login não iniciado.')
    return value


def public_client_path(operation: str) -> Path:
    if operation not in PUBLIC_OPERATIONS:
        raise GoogleClientConfigurationError('O login JARVIS em teste permite somente consultas da agenda.')
    path = bundled_path()
    validate_public_client(path)
    return path


def public_login_status() -> dict:
    """Local availability only: no tokens, client secret, identity or network."""
    try:
        validate_public_client(bundled_path())
    except GoogleClientConfigurationError:
        return {'available': False, 'mode': 'testing', 'operations': []}
    return {'available': True, 'mode': 'testing', 'operations': list(PUBLIC_OPERATIONS)}
