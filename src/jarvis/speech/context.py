"""Vocabulário curto e local para orientar o reconhecimento de fala."""

from pathlib import Path
import re

from jarvis.context.recent import RecentContext


class SpeechContextBuilder:
    STATIC_TERMS = (
        "Jarvis",
        "README.md",
        "pyproject.toml",
        "Markdown",
        "JSON",
        "Python",
        "Obsidian",
        "TXT",
        "MD",
        "underline",
        "underscore",
    )
    MAX_RECENT_FILES = 10
    MAX_RECENT_NOTES = 10
    MAX_TERM_LENGTH = 80

    def __init__(self, recent_context: RecentContext) -> None:
        self.recent_context = recent_context

    def build_hotwords(self) -> str:
        last_tool = self.recent_context.get("last_tool_name")
        obsidian_context = (
            isinstance(last_tool, str) and last_tool.startswith("obsidian_")
        )
        terms = ["Jarvis"]
        if obsidian_context:
            terms.extend(("Obsidian", "Markdown"))
            terms.extend(
                self._names("last_note_results", self.MAX_RECENT_NOTES)
            )
            keys = ("last_note",)
        else:
            terms.extend(self.STATIC_TERMS[1:])
            terms.extend(
                self._names("last_search_results", self.MAX_RECENT_FILES)
            )
            keys = ("last_file",)
        query = self.recent_context.get("last_search_query")
        if isinstance(query, str):
            terms.extend(self._query_terms(query))
        for key in keys:
            value = self.recent_context.get(key)
            if isinstance(value, str):
                terms.append(self._safe_name(value))
        unique = [term for term in dict.fromkeys(terms) if term]
        return ", ".join(unique)

    def _query_terms(self, query: str) -> list[str]:
        safe = " ".join(re.sub(r"[^\w ._-]", " ", query).split())
        return [term[: self.MAX_TERM_LENGTH] for term in safe.split()[:5]]

    def _names(self, key: str, limit: int) -> list[str]:
        values = self.recent_context.get(key)
        if not isinstance(values, tuple):
            return []
        return [self._safe_name(value) for value in values[:limit]]

    def _safe_name(self, value: str) -> str:
        name = Path(value).name[: self.MAX_TERM_LENGTH]
        return " ".join(re.sub(r"[^\w ._-]", " ", name).split())
