"""Adapter lazy para as bibliotecas oficiais Google OAuth."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from jarvis.integrations.google.contracts import CredentialHandle
from jarvis.integrations.google.errors import (
    GoogleAuthorizationDeniedError,
    GoogleClientConfigurationError,
    GoogleDependencyUnavailableError,
    GoogleNetworkError,
    GoogleOAuthStateError,
    GoogleRevocationError,
    GoogleScopeError,
    GoogleTokenRevokedError,
)
from jarvis.integrations.google.models import StoredCredentialMaterial


GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URI = "https://oauth2.googleapis.com/revoke"
MAX_CLIENT_CONFIG_BYTES = 64 * 1024


def _google_imports():
    try:
        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise GoogleDependencyUnavailableError(
            "Integração Google não instalada. Instale o extra 'google'."
        ) from None
    return Credentials, GoogleRequest, InstalledAppFlow, RefreshError, TransportError


def load_client_config(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_CLIENT_CONFIG_BYTES:
            raise ValueError("size")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise GoogleClientConfigurationError(
            "Configuração OAuth Google inválida ou indisponível."
        ) from None
    if not isinstance(value, dict) or set(value) != {"installed"}:
        raise GoogleClientConfigurationError(
            "Use uma credencial OAuth Google do tipo Desktop app."
        )
    installed = value.get("installed")
    if not isinstance(installed, dict):
        raise GoogleClientConfigurationError(
            "Configuração OAuth Google Desktop inválida."
        )
    client_id = installed.get("client_id")
    auth_uri = installed.get("auth_uri")
    token_uri = installed.get("token_uri")
    if (
        not isinstance(client_id, str)
        or not client_id.endswith(".apps.googleusercontent.com")
        or auth_uri not in {
            "https://accounts.google.com/o/oauth2/auth",
            "https://accounts.google.com/o/oauth2/v2/auth",
        }
        or token_uri != GOOGLE_TOKEN_URI
    ):
        raise GoogleClientConfigurationError(
            "Endpoints OAuth Google Desktop não são reconhecidos."
        )
    return value


class GoogleOAuthFlowClient:
    def __init__(
        self,
        client_config_path: Path,
        scopes: tuple[str, ...],
        state: str,
        redirect_uri: str,
    ) -> None:
        _credentials, _request, flow_type, _refresh_error, _transport_error = (
            _google_imports()
        )
        config = load_client_config(client_config_path)
        try:
            self._flow = flow_type.from_client_config(
                config,
                scopes=scopes,
                state=state,
                autogenerate_code_verifier=True,
            )
            self._flow.redirect_uri = redirect_uri
        except Exception:
            raise GoogleClientConfigurationError(
                "Não foi possível iniciar o OAuth Google Desktop."
            ) from None

    def authorization_url(self) -> tuple[str, str]:
        try:
            url, state = self._flow.authorization_url(
                access_type="offline",
                prompt="consent select_account",
            )
            return str(url), str(state)
        except Exception:
            raise GoogleNetworkError(
                "Não foi possível preparar a autorização Google."
            ) from None

    def exchange(self, authorization_response: str) -> GoogleCredentialHandle:
        try:
            parsed = urlsplit(authorization_response)
            if (
                parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or parsed.path != "/oauth2/callback"
            ):
                raise GoogleClientConfigurationError(
                    "Callback OAuth Google não corresponde ao loopback preparado."
                )
            # Mirrors InstalledAppFlow.run_local_server: oauthlib requires an
            # HTTPS scheme for parsing, while Google's desktop redirect itself
            # remains the officially supported HTTP loopback URI.
            oauthlib_response = urlunsplit(
                ("https", parsed.netloc, parsed.path, parsed.query, "")
            )
            options = {}
            if hasattr(self, 'token_exchange_timeout'):
                options['timeout'] = self.token_exchange_timeout
            self._flow.fetch_token(authorization_response=oauthlib_response, **options)
            return GoogleCredentialHandle(self._flow.credentials)
        except GoogleClientConfigurationError:
            raise
        except Exception as error:
            error_name = type(error).__name__.casefold()
            if "state" in error_name:
                raise GoogleOAuthStateError(
                    "Google rejeitou o state da autorização OAuth."
                ) from None
            if error_name == "warning" or "scope" in error_name:
                raise GoogleScopeError(
                    "Google retornou scopes diferentes dos solicitados."
                ) from None
            if "denied" in error_name or "accessdenied" in error_name:
                raise GoogleAuthorizationDeniedError(
                    "Autorização Google negada pelo usuário."
                ) from None
            if "oauth" in error_name or "grant" in error_name:
                raise GoogleClientConfigurationError(
                    "Google rejeitou a troca OAuth do aplicativo Desktop."
                ) from None
            raise GoogleNetworkError(
                "Falha segura ao concluir a autorização Google "
                f"({type(error).__name__})."
            ) from None


class GoogleCredentialHandle:
    def __init__(self, credentials) -> None:
        self._credentials = credentials

    @property
    def valid(self) -> bool:
        return bool(self._credentials.valid)

    @property
    def expired(self) -> bool:
        return bool(self._credentials.expired)

    @property
    def scopes(self) -> tuple[str, ...]:
        granted = getattr(self._credentials, "granted_scopes", None)
        scopes = granted or self._credentials.scopes or ()
        return tuple(sorted(set(scopes)))

    @property
    def access_token(self) -> str:
        token = self._credentials.token
        if not isinstance(token, str) or not token:
            raise GoogleTokenRevokedError(
                "Credencial Google não possui token de acesso válido."
            )
        return token

    def refresh(self) -> None:
        if not self._credentials.refresh_token:
            raise GoogleTokenRevokedError(
                "Credencial Google não pode mais ser renovada."
            )
        _credentials, request_type, _flow, refresh_error, transport_error = (
            _google_imports()
        )
        try:
            self._credentials.refresh(request_type())
        except refresh_error as error:
            if "invalid_grant" in str(error).casefold():
                raise GoogleTokenRevokedError(
                    "Credencial Google foi revogada ou expirou."
                ) from None
            raise GoogleNetworkError(
                "Falha segura ao renovar credencial Google."
            ) from None
        except transport_error:
            raise GoogleNetworkError(
                "Rede indisponível ao renovar credencial Google."
            ) from None
        except Exception:
            raise GoogleNetworkError(
                "Falha segura ao renovar credencial Google."
            ) from None

    def revoke(self) -> None:
        token = self._credentials.refresh_token or self._credentials.token
        if not token:
            return
        body = urlencode({"token": token}).encode("ascii")
        request = Request(
            GOOGLE_REVOKE_URI,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10.0) as response:
                if response.status != 200:
                    raise GoogleRevocationError(
                        "Google não confirmou a revogação remota."
                    )
        except GoogleRevocationError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError):
            raise GoogleRevocationError(
                "Google não confirmou a revogação remota."
            ) from None

    def storage_material(self) -> StoredCredentialMaterial:
        refresh_token = self._credentials.refresh_token
        if not refresh_token:
            raise GoogleTokenRevokedError(
                "Google não forneceu credencial renovável."
            )
        return StoredCredentialMaterial(
            refresh_token=refresh_token,
            token_uri=self._credentials.token_uri,
            client_id=self._credentials.client_id,
            client_secret=self._credentials.client_secret,
            scopes=self.scopes,
        )


def load_google_credentials(
    material: StoredCredentialMaterial,
) -> CredentialHandle:
    credentials_type, _request, _flow, _refresh_error, _transport_error = (
        _google_imports()
    )
    credentials = credentials_type(
        token=None,
        refresh_token=material.refresh_token,
        token_uri=material.token_uri,
        client_id=material.client_id,
        client_secret=material.client_secret,
        scopes=material.scopes,
    )
    return GoogleCredentialHandle(credentials)
