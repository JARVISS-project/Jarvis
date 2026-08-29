import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("google_auth_oauthlib")

from google.auth.exceptions import RefreshError

from jarvis.integrations.google.client import (
    GoogleCredentialHandle,
    GoogleOAuthFlowClient,
)
from jarvis.integrations.google.errors import GoogleTokenRevokedError


def test_google_sdk_adapter_generates_s256_pkce(tmp_path: Path) -> None:
    config = {
        "installed": {
            "client_id": "example.apps.googleusercontent.com",
            "project_id": "jarvis-test",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "client-secret",
            "redirect_uris": ["http://localhost"],
        }
    }
    path = tmp_path / "client.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    client = GoogleOAuthFlowClient(
        path,
        ("openid",),
        "expected-state",
        "http://127.0.0.1:54321/oauth2/callback",
    )

    url, state = client.authorization_url()

    query = parse_qs(urlsplit(url).query)
    verifier = client._flow.code_verifier
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert state == "expected-state"
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [expected_challenge]
    assert query["scope"] == ["openid"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]


def test_google_sdk_refresh_error_never_exposes_upstream_secret() -> None:
    class RevokedCredentials:
        refresh_token = "refresh-secret"

        def refresh(self, _request) -> None:
            raise RefreshError("invalid_grant refresh-secret")

    handle = GoogleCredentialHandle(RevokedCredentials())

    with pytest.raises(GoogleTokenRevokedError) as captured:
        handle.refresh()

    assert "refresh-secret" not in str(captured.value)


def test_exchange_passes_https_parser_url_but_keeps_loopback_redirect() -> None:
    class FakeFlow:
        redirect_uri = "http://127.0.0.1:54321/oauth2/callback"
        credentials = object()

        def __init__(self) -> None:
            self.responses: list[str] = []

        def fetch_token(self, *, authorization_response: str) -> None:
            self.responses.append(authorization_response)

    client = GoogleOAuthFlowClient.__new__(GoogleOAuthFlowClient)
    client._flow = FakeFlow()

    client.exchange(
        "http://127.0.0.1:54321/oauth2/callback?code=secret&state=expected"
    )

    assert client._flow.responses == [
        "https://127.0.0.1:54321/oauth2/callback?code=secret&state=expected"
    ]
    assert client._flow.redirect_uri.startswith("http://127.0.0.1:")
