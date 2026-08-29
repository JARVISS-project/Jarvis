from dataclasses import replace
from datetime import datetime, timedelta, timezone

from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.memory.models import MemoryEntry


def test_add_returns_memory_entry() -> None:
    store = InMemoryMemoryStore()

    entry = store.add("Kaynã prefere café.", "preference")

    assert isinstance(entry, MemoryEntry)
    assert store.get_all() == [entry]


def test_add_generates_unique_ids() -> None:
    store = InMemoryMemoryStore()

    first = store.add("Primeira", "fact")
    second = store.add("Segunda", "fact")

    assert first.id != second.id


def test_add_stores_content() -> None:
    store = InMemoryMemoryStore()

    entry = store.add("Conteúdo importante", "fact")

    assert entry.content == "Conteúdo importante"


def test_add_stores_category() -> None:
    store = InMemoryMemoryStore()

    entry = store.add("Conteúdo", "preference")

    assert entry.category == "preference"


def test_add_sets_created_at() -> None:
    store = InMemoryMemoryStore()

    entry = store.add("Conteúdo", "fact")

    assert isinstance(entry.created_at, datetime)


def test_get_all_lists_memories() -> None:
    store = InMemoryMemoryStore()
    first = store.add("Primeira", "fact")
    second = store.add("Segunda", "preference")

    assert store.get_all() == [first, second]


def test_get_all_preserves_creation_order() -> None:
    store = InMemoryMemoryStore()
    contents = ["Primeira", "Segunda", "Terceira"]

    for content in contents:
        store.add(content, "fact")

    assert [entry.content for entry in store.get_all()] == contents


def test_delete_existing_memory() -> None:
    store = InMemoryMemoryStore()
    entry = store.add("Remover", "fact")

    deleted = store.delete(entry.id)

    assert deleted is True
    assert store.get_all() == []


def test_delete_unknown_id_returns_false() -> None:
    store = InMemoryMemoryStore()

    assert store.delete("inexistente") is False


def test_clear_removes_all_memories() -> None:
    store = InMemoryMemoryStore()
    store.add("Primeira", "fact")
    store.add("Segunda", "fact")

    store.clear()

    assert store.get_all() == []


def test_get_all_returns_list_copy() -> None:
    store = InMemoryMemoryStore()
    entry = store.add("Original", "fact")

    memories = store.get_all()
    memories.clear()

    assert store.get_all() == [entry]


def test_relevant_memory_requires_matching_query_and_consent() -> None:
    store = InMemoryMemoryStore()
    editor = store.add("Editor preferido VS Code", "preference")
    store.add("Bebida preferida café", "preference")

    assert store.get_relevant("editor VS Code") == [editor]

    assert store.update_consent(editor.id, False)
    assert store.get_relevant("editor VS Code") == []


def test_consent_update_preserves_memory_order() -> None:
    store = InMemoryMemoryStore()
    first = store.add("Primeira", "fact")
    second = store.add("Segunda", "fact")

    store.update_consent(first.id, False)

    assert [entry.id for entry in store.get_all()] == [first.id, second.id]


def test_accented_portuguese_terms_are_retrievable() -> None:
    store = InMemoryMemoryStore()
    entry = store.add("Saúde", "preference")

    assert store.get_relevant("saúde") == [entry]


def test_get_all_deletes_expired_memories() -> None:
    store = InMemoryMemoryStore()
    entry = store.add("Temporária", "fact", retention_days=1)
    store._entries[0] = replace(  # noqa: SLF001
        entry,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )

    assert store.get_all() == []
