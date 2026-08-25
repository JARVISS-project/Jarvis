from datetime import datetime
from pathlib import Path

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
