from __future__ import annotations

import hmac
from hashlib import sha256
import ipaddress
import re
from secrets import token_bytes
import unicodedata
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit, urlunsplit

from jarvis.safe_browser.models import BrowserDestination, ValidatedUri
from jarvis.safe_browser.registry import DESTINATIONS, SEARCH_PROVIDERS


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_URL_LIKE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|dev|app|br)\b)"
)
_SYSTEM_COMMAND = re.compile(
    r"(?i)(?:^|\s)(?:cmd|powershell|pwsh|sudo|shutdown|rm\s+-|del\s+|format\s+)(?:\s|$)"
)


class UrlPolicy:
    def __init__(self) -> None:
        self._proof = object()
        self._integrity_key = token_bytes(32)

    def normalize_search_term(self, term: str) -> str:
        if not isinstance(term, str):
            raise ValueError("Termo de busca deve ser texto.")
        normalized = unicodedata.normalize("NFC", term).strip()
        if not normalized:
            raise ValueError("Termo de busca vazio.")
        if len(normalized) > 160:
            raise ValueError("Termo de busca excede o limite.")
        if _CONTROL.search(normalized) or any(
            unicodedata.category(char) in {"Cf", "Cs"} for char in normalized
        ):
            raise ValueError("Termo contém caracteres de controle ou ambíguos.")
        if _URL_LIKE.search(normalized) or re.search(r"%[0-9a-fA-F]{2}", normalized):
            raise ValueError("URL disfarçada não é aceita como termo de busca.")
        if _SYSTEM_COMMAND.search(normalized):
            raise ValueError("Comando de sistema não é termo de busca permitido.")
        return " ".join(normalized.split())

    def destination(self, destination_id: str, association_token: str) -> ValidatedUri:
        try:
            destination = DESTINATIONS[destination_id]
        except KeyError as error:
            raise ValueError("Destino desconhecido.") from error
        canonical = self._canonical_fixed(destination)
        return self._issue(canonical, destination.id, association_token)

    def search(self, provider: str, term: str, association_token: str) -> ValidatedUri:
        normalized = self.normalize_search_term(term)
        try:
            template = SEARCH_PROVIDERS[provider]
        except KeyError as error:
            raise ValueError("Provedor de busca desconhecido.") from error
        encoded = quote_plus(normalized, safe="")
        candidate = template.format(query=encoded)
        canonical = self._canonical_search(candidate, provider, normalized)
        return self._issue(canonical, f"search:{provider}", association_token)

    def revalidate(self, request: ValidatedUri) -> None:
        if request._proof is not self._proof:
            raise PermissionError("URL não foi produzida pela política ativa.")
        expected_integrity = self._integrity(
            request.canonical_url,
            request.destination_id,
            request.association_token,
        )
        if not hmac.compare_digest(request._integrity, expected_integrity):
            raise PermissionError("URL validada foi modificada após o preparo.")
        if request.destination_id.startswith("search:"):
            provider = request.destination_id.split(":", 1)[1]
            split = self._base_checks(request.canonical_url)
            expected_host = urlsplit(SEARCH_PROVIDERS[provider].format(query="x")).hostname
            if split.hostname != expected_host or split.path != "/search":
                raise PermissionError("Template de busca foi alterado.")
            query = parse_qs(split.query, strict_parsing=True, keep_blank_values=True)
            if set(query) != {"q"} or len(query["q"]) != 1 or not query["q"][0]:
                raise PermissionError("Query de busca não corresponde ao template.")
            return
        destination = DESTINATIONS.get(request.destination_id)
        if destination is None or self._canonical_fixed(destination) != request.canonical_url:
            raise PermissionError("URL registrada foi alterada.")

    def is_validated(self, request: ValidatedUri) -> bool:
        try:
            self.revalidate(request)
        except (ValueError, PermissionError, KeyError):
            return False
        return True

    def validate_candidate_for_test(self, url: str) -> str:
        """Validates only registered shapes without granting a launch proof."""
        split = self._base_checks(url)
        canonical = urlunsplit(
            ("https", split.hostname or "", split.path or "/", split.query, "")
        )
        registered = {
            self._canonical_fixed(destination) for destination in DESTINATIONS.values()
        }
        if canonical in registered:
            return canonical
        for provider, template in SEARCH_PROVIDERS.items():
            base = urlsplit(template.format(query="x"))
            if split.hostname != base.hostname or split.path != base.path:
                continue
            query = parse_qs(split.query, strict_parsing=True, keep_blank_values=True)
            if set(query) == {"q"} and len(query["q"]) == 1 and query["q"][0]:
                term = query["q"][0]
                if self.normalize_search_term(term) != term:
                    raise ValueError("Termo do template não está canonicalizado.")
                return self._canonical_search(url, provider, term)
        raise ValueError("URL não corresponde a destino ou template registrado.")

    def _issue(self, canonical_url: str, destination_id: str,
               association_token: str) -> ValidatedUri:
        return ValidatedUri(
            canonical_url,
            destination_id,
            association_token,
            self._proof,
            self._integrity(canonical_url, destination_id, association_token),
        )

    def _integrity(self, canonical_url: str, destination_id: str,
                   association_token: str) -> str:
        value = f"{canonical_url}\0{destination_id}\0{association_token}"
        return hmac.new(
            self._integrity_key,
            value.encode("utf-8"),
            sha256,
        ).hexdigest()

    def _canonical_fixed(self, destination: BrowserDestination) -> str:
        split = self._base_checks(destination.url)
        if split.query or split.fragment:
            raise ValueError("Destino fixo não pode conter query ou fragmento.")
        return urlunsplit(("https", split.hostname or "", split.path or "/", "", ""))

    def _canonical_search(self, url: str, provider: str, term: str) -> str:
        split = self._base_checks(url)
        base = urlsplit(SEARCH_PROVIDERS[provider].format(query="x"))
        if split.hostname != base.hostname or split.path != base.path or split.fragment:
            raise ValueError("URL de busca não corresponde ao template.")
        query = parse_qs(split.query, strict_parsing=True, keep_blank_values=True)
        if set(query) != {"q"} or query["q"] != [term]:
            raise ValueError("Query de busca foi modificada.")
        return urlunsplit(("https", split.hostname or "", split.path, f"q={quote_plus(term, safe='')}", ""))

    @staticmethod
    def _base_checks(url: str):  # type: ignore[no-untyped-def]
        if not isinstance(url, str) or not url or _CONTROL.search(url):
            raise ValueError("URL vazia ou com caracteres de controle.")
        if "\\" in url or unquote(url) != url and "%25" in url.casefold():
            raise ValueError("URL contém barra invertida ou encoding duplo.")
        split = urlsplit(url)
        if split.scheme != "https" or not split.hostname:
            raise ValueError("Somente HTTPS com hostname é permitido.")
        if split.username is not None or split.password is not None:
            raise ValueError("Credenciais em URL são proibidas.")
        try:
            port = split.port
        except ValueError as error:
            raise ValueError("Porta inválida.") from error
        if port not in (None, 443):
            raise ValueError("Porta não permitida.")
        host = split.hostname
        if not host.isascii() or host.startswith("xn--") or ".xn--" in host:
            raise ValueError("Hostname Unicode ou punycode não registrado.")
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("Localhost é proibido.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP literal é proibido.")
        if split.fragment:
            raise ValueError("Fragmento não permitido.")
        return split
