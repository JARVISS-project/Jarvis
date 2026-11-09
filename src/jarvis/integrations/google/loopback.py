"""Callback OAuth local restrito a loopback e com prazo limitado."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Empty, Queue
import time
from threading import Event
from urllib.parse import parse_qs, urlsplit
from hmac import compare_digest

from jarvis.integrations.google.errors import (
    GoogleAuthorizationCancelledError,
    GoogleAuthorizationTimeoutError,
)
from jarvis.integrations.google.models import OAuthCallback


CALLBACK_PATH = "/oauth2/callback"
MAX_CALLBACK_PATH_LENGTH = 8192


class _CallbackHandler(BaseHTTPRequestHandler):
    result_queue: Queue[str]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path != CALLBACK_PATH or len(self.path) > MAX_CALLBACK_PATH_LENGTH:
            self.send_error(404)
            return
        self.result_queue.put_nowait(self.path)
        body = (
            "<!doctype html><html lang='pt-BR'><meta charset='utf-8'>"
            "<title>JARVIS</title><h1>Autorização recebida</h1>"
            "<p>Volte ao JARVIS para verificar a conexão. Você pode fechar esta janela.</p></html>"
        ).encode('utf-8')
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _BoundedHTTPServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(0.25)
        return connection, address

    def handle_error(self, _request, _client_address) -> None:
        return


class LoopbackOAuthReceiver:
    def __init__(self) -> None:
        self._queue: Queue[str] = Queue(maxsize=1)
        handler = type("JarvisOAuthCallbackHandler", (_CallbackHandler,), {})
        handler.result_queue = self._queue
        self._server = _BoundedHTTPServer(("127.0.0.1", 0), handler)
        self._server.timeout = 0.2
        self._closed = False

    @property
    def redirect_uri(self) -> str:
        port = int(self._server.server_address[1])
        return f"http://127.0.0.1:{port}{CALLBACK_PATH}"

    def wait(
        self,
        timeout_seconds: float,
        cancel: Event | None,
        expected_state: str,
    ) -> OAuthCallback:
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise GoogleAuthorizationCancelledError(
                    "Autorização Google cancelada pelo usuário."
                )
            self._server.handle_request()
            try:
                path = self._queue.get_nowait()
            except Empty:
                continue
            query = parse_qs(urlsplit(path).query, keep_blank_values=True)
            state = query.get("state", [None])[0]
            error = query.get("error", [None])[0]
            if state is None or not compare_digest(state, expected_state):
                continue
            return OAuthCallback(
                authorization_response=f"{self.redirect_uri}?{urlsplit(path).query}",
                state=state,
                error=error,
            )
        raise GoogleAuthorizationTimeoutError(
            "Autorização Google expirou antes do callback."
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.server_close()

    def __enter__(self) -> LoopbackOAuthReceiver:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
