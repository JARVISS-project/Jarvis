from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from jarvis.integrations.google.calendar.google_provider import (
    GoogleCalendarProvider,
    GoogleCalendarWriteProvider,
)
from jarvis.integrations.google.calendar.models import (
    CalendarEventInput,
    CalendarReminder,
)
from jarvis.integrations.google.calendar.tools import GoogleCalendarCreateEventTool
from jarvis.integrations.google.errors import (
    GoogleConflictError,
    GoogleNetworkError,
    GoogleResourceNotFoundError,
    GoogleResponseError,
    GoogleWriteUnknownError,
)
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor


FIELDS = frozenset(
    {
        "summary",
        "start",
        "end",
        "is_all_day",
        "timezone",
        "location",
        "description",
        "reminders",
    }
)
OPERATION_ID = "0123456789abcdef0123456789abcdef"


def write_input(**changes: Any) -> CalendarEventInput:
    values: dict[str, Any] = {
        "summary": "Consulta privada",
        "start": datetime(2026, 9, 1, 14, tzinfo=timezone.utc),
        "end": datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
        "timezone": "UTC",
        "location": "Local privado",
        "description": "Descrição privada",
        "reminders": (CalendarReminder("popup", 10),),
    }
    values.update(changes)
    return CalendarEventInput(**values)


class StatefulWriteClient:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.post_calls = 0
        self.patch_calls = 0
        self.patch_etags: list[str] = []
        self.post_failure: str | None = None
        self.patch_failure: str | None = None
        self.fail_next_event_get = False
        self.mutate_readback: tuple[str, object] | None = None
        self.get_paths: list[str] = []

    def get(self, path: str, _params: dict[str, object]) -> dict[str, Any]:
        self.get_paths.append(path)
        if path == "/calendars/primary":
            return {"timeZone": "UTC"}
        event_id = path.rsplit("/", 1)[-1]
        if self.fail_next_event_get:
            self.fail_next_event_get = False
            raise GoogleNetworkError("offline")
        event = self.events.get(event_id)
        if event is None:
            raise GoogleResourceNotFoundError("missing")
        result = deepcopy(event)
        if self.mutate_readback is not None:
            field, value = self.mutate_readback
            result[field] = value
        return result

    def post(self, _path: str, body: dict[str, object]) -> dict[str, Any]:
        self.post_calls += 1
        if self.post_failure == "before" and self.post_calls == 1:
            raise GoogleNetworkError("timeout")
        event_id = str(body["id"])
        if event_id in self.events:
            raise GoogleConflictError("duplicate")
        self.events[event_id] = self._stored(body, event_id, '"v1"')
        if self.post_failure == "after" and self.post_calls == 1:
            raise GoogleNetworkError("timeout")
        if self.post_failure == "malformed_after" and self.post_calls == 1:
            raise GoogleResponseError("malformed success response")
        return deepcopy(self.events[event_id])

    def patch(
        self,
        path: str,
        body: dict[str, object],
        *,
        etag: str,
    ) -> dict[str, Any]:
        self.patch_calls += 1
        self.patch_etags.append(etag)
        event_id = path.rsplit("/", 1)[-1]
        current = self.events[event_id]
        if self.patch_failure == "conflict" or current["etag"] != etag:
            raise GoogleConflictError("stale")
        if self.patch_failure == "before" and self.patch_calls == 1:
            raise GoogleNetworkError("timeout")
        current.update(deepcopy(body))
        current["id"] = event_id
        current["etag"] = '"v2"'
        if self.patch_failure == "after" and self.patch_calls == 1:
            raise GoogleNetworkError("timeout")
        if self.patch_failure == "after_concurrent" and self.patch_calls == 1:
            current["summary"] = "Alteração concorrente"
            current["etag"] = '"v3"'
            raise GoogleNetworkError("timeout")
        return deepcopy(current)

    @staticmethod
    def _stored(
        body: dict[str, object], event_id: str, etag: str
    ) -> dict[str, Any]:
        result = deepcopy(body)
        result.update({"id": event_id, "etag": etag, "status": "confirmed"})
        return result


def test_create_requires_full_readback_match_and_keeps_result_private() -> None:
    client = StatefulWriteClient()
    provider = GoogleCalendarWriteProvider(client)

    created = provider.create_event("primary", write_input(), FIELDS, OPERATION_ID)

    assert created.summary == "Consulta privada"
    assert created.location == "Local privado"
    assert created.description == "Descrição privada"
    assert created.reminders == (CalendarReminder("popup", 10),)
    assert client.post_calls == 1
    assert all(path != "/calendars/primary" for path in client.get_paths)
    assert "Consulta privada" not in repr(created)
    assert "Local privado" not in repr(created)
    assert "Descrição privada" not in repr(created)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("summary", "Outro título"),
        ("start", {"dateTime": "2026-09-01T13:00:00+00:00", "timeZone": "UTC"}),
        ("end", {"dateTime": "2026-09-01T16:00:00+00:00", "timeZone": "UTC"}),
        ("location", "Outro local"),
        ("description", "Outra descrição"),
        ("reminders", {"useDefault": True, "overrides": []}),
    ),
)
def test_create_never_succeeds_when_a_written_field_diverges(
    field: str, value: object
) -> None:
    client = StatefulWriteClient()
    client.mutate_readback = (field, value)

    with pytest.raises(GoogleWriteUnknownError, match="divergiu"):
        GoogleCalendarWriteProvider(client).create_event(
            "primary", write_input(), FIELDS, OPERATION_ID
        )


def test_create_reconciles_timeout_after_commit_without_duplicate() -> None:
    client = StatefulWriteClient()
    client.post_failure = "after"

    event = GoogleCalendarWriteProvider(client).create_event(
        "primary", write_input(), FIELDS, OPERATION_ID
    )

    assert event.summary == "Consulta privada"
    assert client.post_calls == 1
    assert len(client.events) == 1


def test_create_reconciles_malformed_success_response_without_duplicate() -> None:
    client = StatefulWriteClient()
    client.post_failure = "malformed_after"

    event = GoogleCalendarWriteProvider(client).create_event(
        "primary", write_input(), FIELDS, OPERATION_ID
    )

    assert event.summary == "Consulta privada"
    assert client.post_calls == 1
    assert len(client.events) == 1


def test_create_retries_timeout_before_send_with_same_event_identity() -> None:
    client = StatefulWriteClient()
    client.post_failure = "before"

    provider = GoogleCalendarWriteProvider(client)
    first = provider.create_event("primary", write_input(), FIELDS, OPERATION_ID)
    second = provider.create_event("primary", write_input(), FIELDS, OPERATION_ID)

    assert first.id == second.id
    assert client.post_calls == 2
    assert len(client.events) == 1


def test_create_unknown_state_is_terminal() -> None:
    client = StatefulWriteClient()
    client.post_failure = "after"
    original_post = client.post

    def post_then_break_readback(path: str, body: dict[str, object]):
        try:
            return original_post(path, body)
        finally:
            client.fail_next_event_get = True

    client.post = post_then_break_readback  # type: ignore[method-assign]

    with pytest.raises(GoogleWriteUnknownError, match="desconhecido"):
        GoogleCalendarWriteProvider(client).create_event(
            "primary", write_input(), FIELDS, OPERATION_ID
        )
    assert len(client.events) == 1


def test_update_uses_if_match_and_verifies_all_modified_fields() -> None:
    client = StatefulWriteClient()
    provider = GoogleCalendarWriteProvider(client)
    created = provider.create_event("primary", write_input(), FIELDS, OPERATION_ID)
    changed = write_input(
        summary="Consulta alterada",
        start=datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
        end=datetime(2026, 9, 1, 17, tzinfo=timezone.utc),
        location="",
        description="",
        reminders=(),
    )

    updated = provider.update_event("primary", created.id, changed, FIELDS)

    assert updated.summary == "Consulta alterada"
    assert updated.location == ""
    assert updated.description == ""
    assert updated.reminders == ()
    assert client.patch_etags == ['"v1"']


def test_update_conflict_is_terminal_and_does_not_claim_success() -> None:
    client = StatefulWriteClient()
    provider = GoogleCalendarWriteProvider(client)
    created = provider.create_event("primary", write_input(), FIELDS, OPERATION_ID)
    client.patch_failure = "conflict"

    with pytest.raises(GoogleConflictError, match="nada foi sobrescrito"):
        provider.update_event("primary", created.id, write_input(), FIELDS)
    assert client.patch_calls == 1


def test_update_reconciles_timeout_after_commit_without_second_patch() -> None:
    client = StatefulWriteClient()
    provider = GoogleCalendarWriteProvider(client)
    created = provider.create_event("primary", write_input(), FIELDS, OPERATION_ID)
    client.patch_failure = "after"

    updated = provider.update_event(
        "primary", created.id, write_input(summary="Novo título"), FIELDS
    )

    assert updated.summary == "Novo título"
    assert client.patch_calls == 1


def test_update_timeout_followed_by_concurrent_change_remains_unknown() -> None:
    client = StatefulWriteClient()
    provider = GoogleCalendarWriteProvider(client)
    created = provider.create_event("primary", write_input(), FIELDS, OPERATION_ID)
    client.patch_failure = "after_concurrent"

    with pytest.raises(GoogleWriteUnknownError, match="desconhecido"):
        provider.update_event(
            "primary", created.id, write_input(summary="Novo título"), FIELDS
        )
    assert client.patch_calls == 1


def test_write_tool_unknown_is_error_and_audit_contains_no_private_data(
    tmp_path: Path,
) -> None:
    class UnknownProvider:
        def get_timezone(self, _calendar_id):
            return timezone.utc

        def create_event(self, *_args):
            raise GoogleWriteUnknownError("resultado desconhecido")

    audit_path = tmp_path / "audit.log"
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(audit_path),
        CapabilityPolicy({Capability.GOOGLE_CALENDAR_WRITE: True}),
    )
    result = executor.execute_with_result(
        GoogleCalendarCreateEventTool(UnknownProvider(), timezone.utc),
        summary="Título privado",
        start="2026-09-01T14:00:00+00:00",
        end="2026-09-01T15:00:00+00:00",
        timezone="UTC",
        location="Local privado",
        description="Descrição privada",
    )

    assert result.status == "error"
    assert "desconhecido" in result.content
    audit = audit_path.read_text(encoding="utf-8")
    assert "status=error" in audit
    assert "target=google_calendar:event" in audit
    assert "Título privado" not in audit
    assert "Local privado" not in audit
    assert "Descrição privada" not in audit


def test_write_tool_rejects_untyped_optional_payloads() -> None:
    tool = GoogleCalendarCreateEventTool(object(), timezone.utc)
    base = {
        "summary": "Evento",
        "start": "2026-09-01T14:00:00+00:00",
        "end": "2026-09-01T15:00:00+00:00",
        "timezone": "UTC",
    }

    with pytest.raises(ValueError, match="booleano"):
        tool.preflight(**base, is_all_day="false")
    with pytest.raises(ValueError, match="texto"):
        tool.preflight(**base, description={"arbitrary": "payload"})
    with pytest.raises(ValueError, match="timezone ou offset"):
        tool.preflight(**base | {"start": "2026-09-01T14:00:00"})
    with pytest.raises(ValueError, match="minutes"):
        tool.preflight(**base, reminders=[{"method": "popup", "minutes": True}])


def test_read_only_provider_has_no_write_operations() -> None:
    provider = GoogleCalendarProvider(object())

    assert not hasattr(provider, "create_event")
    assert not hasattr(provider, "update_event")


def test_all_day_write_verifies_dates_without_remote_timezone_field() -> None:
    client = StatefulWriteClient()
    event = write_input(
        start=datetime(2026, 9, 7, tzinfo=timezone.utc),
        end=datetime(2026, 9, 8, tzinfo=timezone.utc),
        is_all_day=True,
    )

    verified = GoogleCalendarWriteProvider(client).create_event(
        "primary", event, FIELDS, OPERATION_ID
    )

    assert verified.is_all_day is True
    assert verified.start.date().isoformat() == "2026-09-07"


def test_all_day_write_rejects_same_date_after_normalization() -> None:
    tool = GoogleCalendarCreateEventTool(object(), timezone.utc)

    with pytest.raises(ValueError, match="data final posterior"):
        tool.preflight(
            summary="Inválido",
            start="2026-09-07T10:00:00+00:00",
            end="2026-09-07T11:00:00+00:00",
            timezone="UTC",
            is_all_day=True,
        )


def test_create_reconciles_success_response_with_missing_id() -> None:
    client = StatefulWriteClient()
    original_post = client.post

    def post_without_id(path: str, body: dict[str, object]):
        response = original_post(path, body)
        response.pop("id")
        return response

    client.post = post_without_id  # type: ignore[method-assign]

    verified = GoogleCalendarWriteProvider(client).create_event(
        "primary", write_input(), FIELDS, OPERATION_ID
    )

    assert verified.id == OPERATION_ID


def test_separate_successful_tool_invocations_use_distinct_operation_ids() -> None:
    class CapturingProvider:
        def __init__(self) -> None:
            self.operation_ids: list[str] = []

        def create_event(self, _calendar, _event, _fields, operation_id):
            self.operation_ids.append(operation_id)

    provider = CapturingProvider()
    tool = GoogleCalendarCreateEventTool(provider, timezone.utc)
    arguments = {
        "summary": "Evento repetível",
        "start": "2026-09-01T14:00:00+00:00",
        "end": "2026-09-01T15:00:00+00:00",
        "timezone": "UTC",
    }

    tool.execute(**arguments)
    tool.execute(**arguments)

    assert len(provider.operation_ids) == 2
    assert provider.operation_ids[0] != provider.operation_ids[1]


def test_unknown_tool_retry_reuses_operation_id_for_reconciliation() -> None:
    class ReconcileProvider:
        def __init__(self) -> None:
            self.operation_ids: list[str] = []

        def create_event(self, _calendar, _event, _fields, operation_id):
            self.operation_ids.append(operation_id)
            if len(self.operation_ids) == 1:
                raise GoogleWriteUnknownError("unknown")

    provider = ReconcileProvider()
    tool = GoogleCalendarCreateEventTool(provider, timezone.utc)
    arguments = {
        "summary": "Evento incerto",
        "start": "2026-09-01T14:00:00+00:00",
        "end": "2026-09-01T15:00:00+00:00",
        "timezone": "UTC",
    }

    with pytest.raises(GoogleWriteUnknownError):
        tool.execute(**arguments)
    tool.execute(**arguments)

    assert provider.operation_ids[0] == provider.operation_ids[1]


def test_write_tool_rejects_timezone_offset_mismatch_and_too_many_reminders() -> None:
    tool = GoogleCalendarCreateEventTool(object(), timezone.utc)
    base = {
        "summary": "Evento",
        "start": "2026-09-01T14:00:00+00:00",
        "end": "2026-09-01T15:00:00+00:00",
        "timezone": "America/Sao_Paulo",
    }

    with pytest.raises(ValueError, match="diverge"):
        tool.preflight(**base)
    with pytest.raises(ValueError, match="No máximo 5"):
        tool.preflight(**(
            base
            | {
                "timezone": "UTC",
                "reminders": [
                    {"method": "popup", "minutes": index}
                    for index in range(6)
                ],
            }
        ))
