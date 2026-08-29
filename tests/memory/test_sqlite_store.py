from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from jarvis.memory.sqlite_store import SQLiteMemoryStore


def test_database_is_created_automatically(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "jarvis.db"

    SQLiteMemoryStore(database_path)

    assert database_path.is_file()


def test_add_and_list_memory(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "jarvis.db")

    entry = store.add("Editor preferido: VS Code", "user")

    assert store.get_all() == [entry]
    assert isinstance(entry.created_at, datetime)


def test_add_generates_unique_ids(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "jarvis.db")

    first = store.add("Primeira", "user")
    second = store.add("Segunda", "user")

    assert first.id != second.id


def test_memories_persist_between_instances(tmp_path: Path) -> None:
    database_path = tmp_path / "jarvis.db"
    first_store = SQLiteMemoryStore(database_path)
    entry = first_store.add("Persistente", "user")

    second_store = SQLiteMemoryStore(database_path)

    assert second_store.get_all() == [entry]


def test_get_all_preserves_creation_order(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "jarvis.db")
    contents = ["Primeira", "Segunda", "Terceira"]

    for content in contents:
        store.add(content, "user")

    assert [entry.content for entry in store.get_all()] == contents


def test_delete_removes_existing_memory(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "jarvis.db")
    entry = store.add("Remover", "user")

    assert store.delete(entry.id) is True
    assert store.get_all() == []
    assert store.delete(entry.id) is False


def test_clear_removes_all_memories(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "jarvis.db")
    store.add("Primeira", "user")
    store.add("Segunda", "user")

    store.clear()

    assert store.get_all() == []


def test_existing_memory_table_is_migrated_without_data_loss(tmp_path: Path) -> None:
    database_path = tmp_path / "jarvis.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE memories (
                creation_order INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO memories (id, content, category, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("legacy", "Editor preferido", "user", datetime.now().isoformat()),
        )

    store = SQLiteMemoryStore(database_path)

    entries = store.get_all()
    assert len(entries) == 1
    assert entries[0].id == "legacy"
    assert entries[0].consent_given is True


def test_sqlite_consent_controls_relevant_retrieval(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "jarvis.db")
    entry = store.add("Editor preferido VS Code", "preference")

    assert store.get_relevant("editor VS Code") == [entry]
    assert store.update_consent(entry.id, False)
    assert store.get_relevant("editor VS Code") == []


def test_sqlite_store_deletes_expired_memory_on_read(tmp_path: Path) -> None:
    database_path = tmp_path / "jarvis.db"
    store = SQLiteMemoryStore(database_path)
    entry = store.add("Temporária", "fact", retention_days=1)
    expired_at = datetime.now(timezone.utc) - timedelta(days=2)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?",
            (expired_at.isoformat(), entry.id),
        )

    assert store.get_relevant("temporária") == []
    with sqlite3.connect(database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert count == 0
