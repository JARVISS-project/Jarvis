"""One-shot native Picker OAuth redirect, without exposing tokens to local HTML."""
from __future__ import annotations

from dataclasses import dataclass, field
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler
import re
from threading import Event
import time
from urllib.parse import parse_qs, urlsplit

from jarvis.integrations.google.errors import (
    GoogleAuthorizationCancelledError, GoogleAuthorizationDeniedError,
    GoogleAuthorizationTimeoutError, GoogleOAuthStateError,
)
from jarvis.integrations.google.loopback import _BoundedHTTPServer, CALLBACK_PATH
from jarvis.integrations.google.models import OAuthCallback
from jarvis.integrations.google.scopes import DRIVE_PICKER_SCOPES


@dataclass(frozen=True)
class PickerCallback:
    oauth: OAuthCallback = field(repr=False)
    file_ids: tuple[str, ...] = field(repr=False)


class PickerOAuthReceiver:
    def __init__(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                status = owner.accept(self.path, self.headers.get('Host', ''),
                                      self.headers.get('Origin'), self.headers.get('Referer'))
                body = b'JARVIS: callback received. Return to the application.'
                self.send_response(status)
                for key, value in (('Content-Type', 'text/plain'), ('Cache-Control', 'no-store'),
                                   ('Referrer-Policy', 'no-referrer'),
                                   ('Content-Security-Policy', "default-src 'none'"),
                                   ('Content-Length', str(len(body)))):
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802
                self.send_error(405)

        self._server = _BoundedHTTPServer(('127.0.0.1', 0), Handler)
        self._server.timeout = 0.2
        self._closed = False
        self._used = False
        self._expected = None
        self._deadline = 0.
        self._result = None
        self._delivered = False
        self._error = None

    @property
    def redirect_uri(self) -> str:
        return f'http://127.0.0.1:{self._server.server_address[1]}{CALLBACK_PATH}'

    def arm(self, nonce: str, timeout_seconds: float) -> None:
        if self._expected is not None or self._closed or not 0 < timeout_seconds <= 120:
            raise GoogleOAuthStateError('Sessão Picker inválida.')
        self._expected = nonce
        self._deadline = time.monotonic() + timeout_seconds

    def accept(self, path: str, host: str, origin: str | None = None,
               referer: str | None = None) -> int:
        if self._closed or self._used or self._expected is None or time.monotonic() >= self._deadline:
            return 410
        if host != urlsplit(self.redirect_uri).netloc:
            return 403
        origins = {'https://accounts.google.com', 'https://docs.google.com'}
        if origin is not None and origin not in origins:
            return 403
        if referer is not None:
            parts = urlsplit(referer)
            if f'{parts.scheme}://{parts.netloc}' not in origins:
                return 403
        parsed = urlsplit(path)
        if (len(path) > 8192 or parsed.path != CALLBACK_PATH or parsed.scheme
                or parsed.netloc or parsed.fragment):
            return 400
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
        except ValueError:
            return 400
        state = query.get('state', [])
        if len(state) != 1 or not compare_digest(state[0], self._expected):
            return 403
        # A valid nonce can be consumed exactly once, including denial/malformed response.
        self._used = True
        allowed = {'state', 'code', 'scope', 'picked_file_ids', 'error',
                   'error_description', 'authuser', 'prompt', 'iss'}
        if set(query) - allowed or any(len(v) != 1 for v in query.values()):
            self._error = GoogleOAuthStateError('Callback Picker malformado.')
            return 400
        # Google includes RFC 9207 issuer identification in OAuth redirects.
        # Older callbacks may omit it; a present issuer must match exactly.
        if 'iss' in query and query['iss'] != ['https://accounts.google.com']:
            self._error = GoogleOAuthStateError('Emissor OAuth inválido.')
            return 400
        if query.get('error'):
            self._error = GoogleAuthorizationDeniedError('Seleção Google cancelada/negada.')
            return 200
        ids = tuple(query.get('picked_file_ids', [''])[0].split(','))
        if (not query.get('code', [''])[0] or query.get('scope') != [DRIVE_PICKER_SCOPES[0]]
                or not 1 <= len(ids) <= 20 or len(set(ids)) != len(ids)
                or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value) for value in ids)):
            self._error = GoogleOAuthStateError('Callback Picker inválido; nenhuma autorização local.')
            return 400
        self._result = PickerCallback(OAuthCallback(
            self.redirect_uri + '?' + parsed.query, state[0]), ids)
        return 200

    def wait(self, cancel: Event | None = None) -> PickerCallback:
        while not self._closed and time.monotonic() < self._deadline:
            if cancel is not None and cancel.is_set():
                raise GoogleAuthorizationCancelledError('Seleção Google cancelada localmente.')
            if self._error is not None:
                raise self._error
            if self._result is not None:
                if self._delivered:
                    raise GoogleOAuthStateError('Callback Picker já entregue; replay bloqueado.')
                self._delivered = True
                return self._result
            self._server.handle_request()
        raise GoogleAuthorizationTimeoutError('Seleção Google expirou; nenhuma autorização local.')

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._expected = None
            self._result = None
            self._server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
