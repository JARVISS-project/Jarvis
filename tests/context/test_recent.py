import pytest

from jarvis.context.recent import RecentContext


def test_recent_context_stores_typed_references_and_ordered_results() -> None:
    context = RecentContext()
    context.set("last_file", "README.md")
    context.set("last_directory", "src")
    context.set("last_search_results", ["a.md", "b.md"])
    assert context.get("last_file") == "README.md"
    assert context.get("last_directory") == "src"
    assert context.get("last_search_results") == ("a.md", "b.md")


def test_recent_context_rejects_raw_content_and_can_clear() -> None:
    context = RecentContext()
    with pytest.raises(ValueError):
        context.set("file_content", "segredo")
    context.set("last_file", "README.md")
    context.clear()
    assert context.get("last_file") is None
