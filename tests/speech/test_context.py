from jarvis.context.recent import RecentContext
from jarvis.speech.context import SpeechContextBuilder


def test_hotwords_are_limited_to_names_from_recent_context() -> None:
    recent = RecentContext()
    recent.set(
        "last_search_results",
        [f"src/arquivo_{index}.py" for index in range(8)],
    )
    recent.set(
        "last_note_results",
        [f"Notas/nota_{index}.md" for index in range(8)],
    )
    hotwords = SpeechContextBuilder(recent).build_hotwords()
    assert "README.md" in hotwords
    assert "arquivo_7.py" in hotwords
    assert "nota_4.md" not in hotwords
    assert "src/" not in hotwords
    assert "Notas/" not in hotwords
    for term in (
        "ORCA",
        "WhatsApp",
        "VS Code",
        "GitHub",
        "Ollama",
        "OpenAI",
        "Gemini",
    ):
        assert term in hotwords


def test_obsidian_context_uses_notes_and_short_previous_query() -> None:
    recent = RecentContext()
    recent.set("last_tool_name", "obsidian_search_notes")
    recent.set(
        "last_note_results",
        [f"Notas/Teste {index}.md" for index in range(12)],
    )
    recent.set("last_search_query", "notas sobre teste do Jarvis")
    hotwords = SpeechContextBuilder(recent).build_hotwords()
    assert "Teste 9.md" in hotwords
    assert "Teste 10.md" not in hotwords
    assert "teste" in hotwords
    assert "Obsidian" in hotwords
    assert "Markdown" in hotwords
    assert "ORCA" in hotwords
    assert "GitHub" in hotwords
