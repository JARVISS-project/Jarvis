"""Small Calendar writes with explicit local selection and executor receipts."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from threading import RLock
from types import MappingProxyType
from typing import Any
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from jarvis.integrations.google.calendar.models import CalendarEventInput
from jarvis.integrations.google.calendar.operation_journal import CalendarOperationJournal
from jarvis.integrations.google.operation_lock import GoogleOperationLock
from jarvis.integrations.google.calendar.tools import _build_event_input, _validate_write_arguments
from jarvis.integrations.google.errors import GoogleIntegrationError, GoogleWriteUnknownError
from jarvis.security.authorization import AuthorizationReceipt
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation

_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})$")
_EVENT = re.compile(r"^[a-zA-Z0-9_-]{1,1024}$")


@dataclass(frozen=True, slots=True)
class PreparedCalendarWrite:
    payload: str = field(repr=False)
    grant: str = field(repr=False)
    event: CalendarEventInput = field(repr=False)
    modified: frozenset[str]
    operation_id: str
    etag: str | None
    conflicts: int
    calendar_label: str = field(repr=False)
    calendar_version: str
    account_label: str = field(default='', repr=False)
    previous: str = field(default='', repr=False)


@dataclass(frozen=True, slots=True)
class AuthorizedCalendarWrite:
    prepared: PreparedCalendarWrite


class _LimitedWriteTool(Tool):
    risk = RiskLevel.EXTERNAL_EFFECT
    requires_authorization_receipt = True
    confirmation_timeout_seconds = 30.0

    def __init__(self, provider: Any, calendars: dict[str, str],
                 grant_binding: Callable[[], str],
                 policy_current: Callable[[], dict[str, str]] | None = None,
                 journal: CalendarOperationJournal | None = None,
                 account_label: Callable[[], str] | None = None) -> None:
        self.provider = provider
        self._account_label = account_label
        self.calendar_aliases = MappingProxyType(dict(calendars))
        self._grant_binding = grant_binding
        self._policy_current = policy_current or (lambda: dict(self.calendar_aliases))
        self._lock = RLock()
        self._journal = journal or CalendarOperationJournal()
        self._selections: dict[str, tuple[str, str, str]] = {}
        self._authorized: dict[int, AuthorizedCalendarWrite] = {}

    def register_selection(self, calendar_id: str, event_id: str) -> str:
        """Only deterministic user routing calls this; no API effect or authority."""
        if calendar_id not in self.calendar_aliases.values() or not _EVENT.fullmatch(event_id):
            raise PermissionError("Seleção explícita inválida.")
        token = uuid4().hex
        with self._lock:
            self._selections.clear()
            self._selections[token] = (self._grant_binding(), calendar_id, event_id)
        return token

    def preflight(self, **kwargs: Any) -> None:
        if set(kwargs) - set(self.parameters["properties"]):
            raise ValueError("Argumentos extras não permitidos.")
        calendar = kwargs.get("calendar_id")
        if (calendar not in self.calendar_aliases.values()
                or calendar not in self._policy_current().values()):
            raise PermissionError("Calendário não autorizado explicitamente.")
        if self.name == "google_calendar_create_event":
            _validate_write_arguments(kwargs, timezone.utc)
        else:
            selected = self._selections.get(kwargs.get("selection", ""))
            if selected != (self._grant_binding(), calendar, kwargs.get("event_id")):
                raise PermissionError("Selecione explicitamente o evento nesta interação.")
            title = "summary" in kwargs
            times = "start" in kwargs or "end" in kwargs or "timezone" in kwargs
            if title == times:
                raise ValueError("Altere somente título ou somente horário por interação.")
            if title and (not isinstance(kwargs["summary"], str) or not kwargs["summary"].strip()
                          or len(kwargs["summary"]) > 1024):
                raise ValueError("Título obrigatório, limitado a 1024 caracteres.")
            if times and not all(k in kwargs for k in ("start", "end", "timezone")):
                raise ValueError("Informe início, fim e timezone; nenhum dado será inferido.")
        if "start" in kwargs:
            if not all(isinstance(kwargs.get(k), str) and _TIME.fullmatch(kwargs[k])
                       for k in ("start", "end")):
                raise ValueError("Use datas absolutas ISO 8601 e offset explícito.")
            values = {**kwargs, "summary": kwargs.get("summary", "Horário selecionado")}
            event = _build_event_input(values, timezone.utc)
            duration = (event.end.astimezone(timezone.utc) - event.start.astimezone(timezone.utc)).total_seconds()
            if not 0 < duration <= 86400:
                raise ValueError("Duração deve ser positiva e no máximo 24 horas.")

    def get_target(self, **kwargs: Any) -> str:
        digest = hashlib.sha256(json.dumps(kwargs, sort_keys=True).encode()).hexdigest()
        return "google_calendar:write:" + digest

    def prepare(self, **kwargs: Any) -> ToolPreparation:
        with self._lock:
            self.preflight(**kwargs)
            grant = self._grant_binding()
            calendar = kwargs["calendar_id"]
            calendar_version = self.provider.private_calendar_version(calendar)
            etag = None
            previous = ''
            modified = frozenset({"summary", "start", "end", "timezone"})
            if self.name == "google_calendar_update_event":
                # UTC here is only a parsing fallback. Explicit event timezone is required below.
                original = self.provider.get_event(calendar, kwargs["event_id"], timezone.utc)
                if original is None or not original.etag or not original.simple_owned or original.is_all_day:
                    raise PermissionError("Evento ausente, recorrente, compartilhado ou sem versão segura.")
                etag = original.etag
                previous = (f"Anterior: {original.summary}; início {original.start.isoformat()}, "
                            f"fim {original.end.isoformat()}, timezone {original.timezone_name}. ")
                if "summary" in kwargs:
                    zone = original.timezone_name
                    if not zone:
                        raise PermissionError("Evento sem timezone explícito; alteração bloqueada.")
                    values = {**kwargs, "start": original.start.astimezone(ZoneInfo(zone)).isoformat(),
                              "end": original.end.astimezone(ZoneInfo(zone)).isoformat(), "timezone": zone}
                    modified = frozenset({"summary"})
                else:
                    values = {**kwargs, "summary": original.summary}
                    modified = frozenset({"start", "end", "timezone"})
                event = _build_event_input(values, timezone.utc)
            else:
                event = _build_event_input(kwargs, timezone.utc)
                # No inherited default popup/email reminders for new test events.
                values = {**kwargs, "reminders": []}
                event = _build_event_input(values, timezone.utc)
                modified = modified | {"reminders"}
            conflicts = self.provider.overlap_count(calendar, event.start, event.end, kwargs.get("event_id"))
            payload = json.dumps(kwargs, sort_keys=True, separators=(",", ":"))
            key = hashlib.sha256((grant + payload).encode()).hexdigest()
            operation = (self._journal.reserve(key) if self.name == "google_calendar_create_event"
                         else hashlib.sha256((key + str(etag)).encode()).hexdigest()[:32])
            label = next(alias for alias, value in self.calendar_aliases.items() if value == calendar)
            prepared = PreparedCalendarWrite(payload, grant, event, modified, operation, etag,
                                             conflicts, label, calendar_version,
                                             self._account_label() if self._account_label else '', previous)
            return ToolPreparation(self.get_target(**kwargs), prepared, self._binding(prepared))

    def preflight_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> None:
        self.preflight(**kwargs)
        prepared = self._prepared(preparation)
        if prepared.grant != self._grant_binding() or prepared.payload != json.dumps(kwargs, sort_keys=True, separators=(",", ":")):
            raise PermissionError("Conta ou payload mudou depois da preparação.")

    def confirmation_prompt(self, preparation: ToolPreparation) -> str:
        p = self._prepared(preparation)
        action = "Criar" if self.name == "google_calendar_create_event" else "Alterar"
        conflict = f" Há {p.conflicts} conflito(s) de horário; você pode prosseguir." if p.conflicts else " Sem conflito de horário observado."
        account = f"Conta {p.account_label}. " if p.account_label else ""
        return (f"{account}{p.previous}{action} no calendário {p.calendar_label}: {p.event.summary}. "
                f"Início {p.event.start.isoformat()}, fim {p.event.end.isoformat()}, timezone {p.event.timezone}."
                f"{conflict} Sem convidados ou notificações. Confirma esta operação?")

    def bind_authorized(self, preparation: ToolPreparation,
                        authorization: object | None = None) -> ToolPreparation:
        if not isinstance(authorization, AuthorizationReceipt) or not authorization.claim(
                self.name, preparation.target, preparation.binding_digest):
            raise PermissionError("Escrita exige comprovante do executor, vinculado e de uso único.")
        p = self._prepared(preparation)
        self.preflight_prepared(preparation, **json.loads(p.payload))
        if self._binding(p) != preparation.binding_digest:
            raise PermissionError("Prévia ou versão Calendar mudou depois da confirmação.")
        artifact = AuthorizedCalendarWrite(p)
        self._authorized[id(artifact)] = artifact
        return ToolPreparation(preparation.target, artifact, preparation.binding_digest)

    def execute(self, **kwargs: Any) -> str:
        raise PermissionError("Escrita exige preparação, confirmação e executor.")

    def execute_prepared(self, preparation: ToolPreparation, **kwargs: Any) -> str:
        raise PermissionError("Preparação sem autorização não executa escrita.")

    def execute_authorized(self, preparation: ToolPreparation) -> str:
        if not isinstance(preparation.context, AuthorizedCalendarWrite):
            raise PermissionError("Operação autorizada ausente.")
        if self._authorized.pop(id(preparation.context), None) is not preparation.context:
            raise PermissionError("Operação forjada ou já executada.")
        p = preparation.context.prepared
        args = json.loads(p.payload)
        with self._lock, GoogleOperationLock(mutex_name=r'Local\JARVIS.CalendarWrite.Execute'):
            self.preflight(**args)
            if p.grant != self._grant_binding():
                raise PermissionError("Conta Calendar mudou antes da execução.")
            if self._binding(p) != preparation.binding_digest:
                raise PermissionError("Payload ou versão mudou antes da escrita.")
            if self.provider.private_calendar_version(args["calendar_id"]) != p.calendar_version:
                raise PermissionError("Compartilhamento Calendar mudou depois da prévia.")
            try:
                if self.name == "google_calendar_create_event":
                    verified = self.provider.create_event(args["calendar_id"], p.event, p.modified, p.operation_id)
                else:
                    verified = self.provider.update_event(args["calendar_id"], args["event_id"], p.event,
                                                          p.modified, expected_etag=p.etag)
                if not verified.simple_owned:
                    raise GoogleWriteUnknownError("Evento verificado fora do escopo simples.")
                try:
                    if self.provider.private_calendar_version(args["calendar_id"]) != p.calendar_version:
                        raise GoogleWriteUnknownError("Compartilhamento alterado durante a escrita.")
                except (PermissionError, GoogleIntegrationError):
                    raise GoogleWriteUnknownError("Compartilhamento final não pôde ser verificado.") from None
            except GoogleWriteUnknownError:
                raise RuntimeError("Escrita pendente de reconciliação por leitura; não repita a operação.") from None
            except GoogleIntegrationError:
                raise RuntimeError("Calendar recusou a operação ou está indisponível; nenhum sucesso confirmado.") from None
            key = hashlib.sha256((p.grant + p.payload).encode()).hexdigest()
            if self.name == "google_calendar_create_event":
                self._journal.complete(key, p.operation_id)
            if self.name == "google_calendar_update_event":
                self._selections.clear()
            return "Evento criado e verificado no Google Calendar." if self.name == "google_calendar_create_event" else "Evento alterado e verificado no Google Calendar."

    @staticmethod
    def _binding(p: PreparedCalendarWrite) -> str:
        return hashlib.sha256(json.dumps({
            "grant": p.grant, "payload": p.payload, "etag": p.etag,
            "calendar_version": p.calendar_version,
            "operation": p.operation_id, "modified": sorted(p.modified),
            "account_label": p.account_label, "previous": p.previous,
            "event": [p.event.summary, p.event.start.isoformat(), p.event.end.isoformat(), p.event.timezone],
        }, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _prepared(preparation: ToolPreparation) -> PreparedCalendarWrite:
        if not isinstance(preparation.context, PreparedCalendarWrite):
            raise PermissionError("Preparação Calendar inválida.")
        return preparation.context


_BASE = {"calendar_id": {"type": "string"}, "summary": {"type": "string"},
         "start": {"type": "string"}, "end": {"type": "string"}, "timezone": {"type": "string"}}


class LimitedCalendarCreateTool(_LimitedWriteTool):
    name = "google_calendar_create_event"
    risk = RiskLevel.EXTERNAL_EFFECT
    description = "Cria evento simples em calendário autorizado após prévia e confirmação."
    parameters = {"type": "object", "properties": _BASE,
                  "required": list(_BASE), "additionalProperties": False}


class LimitedCalendarUpdateTool(_LimitedWriteTool):
    name = "google_calendar_update_event"
    risk = RiskLevel.EXTERNAL_EFFECT
    description = "Altera título ou horário de evento explicitamente selecionado."
    parameters = {"type": "object", "properties": {**_BASE, "event_id": {"type": "string"},
                  "selection": {"type": "string"}}, "required": ["calendar_id", "event_id", "selection"],
                  "additionalProperties": False}
