from datetime import datetime, timedelta, timezone

from jarvis.security.authorization import AuthorizationGrantStore
from jarvis.security.audit import AuditLogger
from jarvis.security.filesystem import FileSystemGuard
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.executor import ToolExecutor


def test_grant_is_bound_to_tool_target_and_single_use() -> None:
    store = AuthorizationGrantStore()
    grant = store.issue("delete_file", "C:/vault/a.txt", "interaction-1")
    assert not store.consume(grant, "edit_file", "C:/vault/a.txt")
    assert not store.consume(grant, "delete_file", "C:/vault/b.txt")
    assert not store.consume(
        grant, "delete_file", "C:/vault/a.txt", "interaction-2"
    )
    assert store.consume(
        grant, "delete_file", "C:\\vault\\a.txt", "interaction-1"
    )
    assert grant.consumed
    assert not store.consume(grant, "delete_file", "C:/vault/a.txt")


def test_expired_and_invalidated_grants_are_rejected() -> None:
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    current = [now]
    store = AuthorizationGrantStore(2, clock=lambda: current[0])
    expired = store.issue("delete_file", "a.txt")
    current[0] = now + timedelta(seconds=3)
    assert not store.consume(expired, "delete_file", "a.txt")
    current[0] = now
    invalidated = store.issue("delete_file", "a.txt")
    store.invalidate_all()
    assert invalidated.consumed
    assert not store.consume(invalidated, "delete_file", "a.txt")


def test_session_grant_rejects_changed_arguments_and_scoped_invalidation() -> None:
    store = AuthorizationGrantStore()
    first = store.issue(
        "critical_tool",
        "target-a",
        "interaction-a",
        "session-a",
        "digest-a",
    )
    second = store.issue(
        "critical_tool",
        "target-b",
        "interaction-b",
        "session-b",
        "digest-b",
    )

    assert not store.consume(
        first,
        "critical_tool",
        "target-a",
        "interaction-a",
        "session-a",
        "changed-digest",
    )
    store.invalidate_session("session-a")
    assert first.consumed
    assert store.consume(
        second,
        "critical_tool",
        "target-b",
        "interaction-b",
        "session-b",
        "digest-b",
    )


def test_session_grant_rejects_changed_prepared_identity() -> None:
    store = AuthorizationGrantStore()
    grant = store.issue(
        "critical_tool",
        "janela:Obsidian",
        "interaction-a",
        "session-a",
        "arguments-a",
        "prepared-a",
    )

    assert not store.consume(
        grant,
        "critical_tool",
        "janela:Obsidian",
        "interaction-a",
        "session-a",
        "arguments-a",
        "prepared-b",
    )
    assert store.consume(
        grant,
        "critical_tool",
        "janela:Obsidian",
        "interaction-a",
        "session-a",
        "arguments-a",
        "prepared-a",
    )


class BooleanOnlySafetyManager:
    def authorize(self, action, target, risk):  # type: ignore[no-untyped-def]
        return True


def test_high_tool_cannot_use_legacy_boolean_as_authorization(
    tmp_path,
) -> None:
    target = tmp_path / "preservado.txt"
    target.write_text("x", encoding="utf-8")
    executor = ToolExecutor(
        BooleanOnlySafetyManager(),  # type: ignore[arg-type]
        AuditLogger(tmp_path / "audit.log"),
    )
    result = executor.execute_with_result(
        DeleteFileTool(FileSystemGuard(tmp_path)), path="preservado.txt"
    )
    assert result.status == "denied"
    assert target.exists()
