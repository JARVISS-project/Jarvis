"""Bounded GET-only transport; endpoints are internal constants, not LLM input."""
from __future__ import annotations

import json
import hashlib
import re
import time
from contextlib import nullcontext
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from jarvis.integrations.google.errors import (
    GoogleNetworkError, GooglePermissionError, GoogleRateLimitError,
    GoogleResourceNotFoundError, GoogleResponseError, GoogleTokenRevokedError,
)


def grant_digest(material: Any) -> str:
    return hashlib.sha256((material.client_id + '\0' + material.refresh_token).encode()).hexdigest()


class DriveDocsClient:
    def __init__(self, auth: Any, grant_binding: Any, opener: Any = urlopen) -> None:
        self.auth = auth
        self.grant_binding = grant_binding  # local DPAPI material, never an API request
        self._opener = opener

    def get(self, service: str, path: str, params: dict[str, object], *, deadline: float | None = None) -> dict:
        guard = getattr(self.auth, 'request_guard', nullcontext)
        with guard():
            return self._get_guarded(service, path, params, deadline=deadline)

    def _get_guarded(self, service, path, params, *, deadline=None):
        bases = {'drive': 'https://www.googleapis.com/drive/v3',
                 'docs': 'https://docs.googleapis.com/v1'}
        pattern = r'/(?:about|files(?:/[A-Za-z0-9_-]{1,256})?)' if service == 'drive' else r'/documents/[A-Za-z0-9_-]{1,256}'
        if service not in bases or not re.fullmatch(pattern, path):
            raise PermissionError('Endpoint não permitido.')
        binding = self.grant_binding()
        handle = self.auth.credentials()
        if grant_digest(handle.storage_material()) != binding or self.grant_binding() != binding:
            raise PermissionError('Conta/grant mudou antes da leitura; nenhuma consulta executada.')
        try:
            return self._once(bases[service] + path, params, handle.access_token, deadline)
        except GoogleTokenRevokedError:
            # Existing auth lifecycle: one refresh on 401, never blind retries.
            handle = self.auth.refresh_credentials()
            if grant_digest(handle.storage_material()) != binding or self.grant_binding() != binding:
                raise PermissionError('Grant alterado durante refresh; selecione novamente.')
            return self._once(bases[service] + path, params, handle.access_token, deadline)

    def _once(self, endpoint: str, params: dict, token: str, deadline: float | None) -> dict:
        remaining = 10. if deadline is None else min(10., deadline - time.monotonic())
        if remaining <= 0:
            raise GoogleNetworkError('Prazo de leitura excedido.')
        request = Request(endpoint + '?' + urlencode(params),
                          headers={'Authorization': 'Bearer ' + token}, method='GET')
        try:
            with self._opener(request, timeout=remaining) as response:
                raw = response.read(1_048_577)
        except HTTPError as error:
            kinds = {401: GoogleTokenRevokedError, 403: GooglePermissionError,
                     404: GoogleResourceNotFoundError, 429: GoogleRateLimitError}
            raise kinds.get(error.code, GoogleNetworkError)('Google indisponível; resposta privada omitida.') from None
        except (URLError, OSError, TimeoutError):
            raise GoogleNetworkError('Rede indisponível; nenhum arquivo confirmado.') from None
        if len(raw) > 1_048_576:
            raise GoogleResponseError('Resposta excede 1 MiB.')
        try:
            value = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise GoogleResponseError('Resposta Google inválida.') from None
        if not isinstance(value, dict):
            raise GoogleResponseError('Resposta Google inválida.')
        return value
