"""Cliente HTTP mínimo e autorizado para a API Google Calendar."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from jarvis.integrations.google.contracts import GoogleAuthProvider
from jarvis.integrations.google.errors import (
    GoogleNetworkError,
    GooglePermissionError,
    GoogleRateLimitError,
    GoogleResourceNotFoundError,
    GoogleResponseError,
    GoogleTokenRevokedError,
)


GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_ERROR_RESPONSE_BYTES = 64 * 1024
RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})


class GoogleCalendarApiClient:
    def __init__(self, auth: GoogleAuthProvider) -> None:
        self._auth = auth

    def get(self, path: str, params: dict[str, object]) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict[str, object]) -> dict[str, Any]:
        return self._request("POST", path, body=body)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        handle = self._auth.credentials()
        try:
            return self._request_once(
                method, path, params or {}, body, handle.access_token
            )
        except GoogleTokenRevokedError:
            handle = self._auth.refresh_credentials()
            return self._request_once(
                method, path, params or {}, body, handle.access_token
            )

    def _request_once(
        self,
        method: str,
        path: str,
        params: dict[str, object],
        body: dict[str, object] | None,
        access_token: str,
    ) -> dict[str, Any]:
        query = urlencode(params)
        url = f"{GOOGLE_CALENDAR_API_BASE}{path}"
        if query:
            url = f"{url}?{query}"
        headers = {"Authorization": f"Bearer {access_token}"}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30.0) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if error.code == 401:
                raise GoogleTokenRevokedError(
                    "Google Calendar rejeitou a credencial."
                ) from None
            if error.code == 404:
                raise GoogleResourceNotFoundError(
                    "Recurso do Google Calendar não encontrado."
                ) from None
            if error.code == 429:
                raise GoogleRateLimitError(
                    "Limite temporário do Google Calendar atingido."
                ) from None
            if error.code == 403:
                if self._is_rate_limit_error(error):
                    raise GoogleRateLimitError(
                        "Limite temporário do Google Calendar atingido."
                    ) from None
                raise GooglePermissionError(
                    "Google Calendar API não está habilitada ou permitida."
                ) from None
            raise GoogleNetworkError(
                "Google Calendar recusou a consulta."
            ) from None
        except (URLError, TimeoutError, OSError):
            raise GoogleNetworkError(
                "Rede indisponível ao consultar Google Calendar."
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GoogleResponseError("Resposta do Google Calendar excedeu o limite.")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise GoogleResponseError("Resposta inválida do Google Calendar.") from None
        if not isinstance(value, dict):
            raise GoogleResponseError("Resposta inválida do Google Calendar.")
        return value

    @staticmethod
    def _is_rate_limit_error(error: HTTPError) -> bool:
        try:
            raw = error.read(MAX_ERROR_RESPONSE_BYTES + 1)
        except (AttributeError, OSError, ValueError):
            return False
        if not isinstance(raw, bytes) or len(raw) > MAX_ERROR_RESPONSE_BYTES:
            return False
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        error_payload = payload.get("error")
        if not isinstance(error_payload, dict):
            return False
        entries = error_payload.get("errors")
        if not isinstance(entries, list):
            return False
        return any(
            isinstance(entry, dict)
            and entry.get("reason") in RATE_LIMIT_REASONS
            for entry in entries
        )
