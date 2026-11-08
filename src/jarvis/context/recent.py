"""Referências operacionais efêmeras, sem conteúdo bruto."""

from typing import TypeAlias


ContextValue: TypeAlias = str | tuple[str, ...]


class RecentContext:
    ALLOWED_KEYS = frozenset(
        {
            "last_file",
            "last_active_file",
            "last_directory",
            "last_note",
            "last_active_note",
            "last_note_results",
            "last_file_results",
            "last_search_results",
            "last_tool_name",
            "last_entities",
            "last_references",
            "last_search_query",
            "last_desktop_window",
        }
    )

    def __init__(self) -> None:
        self._values: dict[str, ContextValue] = {}

    def set(self, key: str, value: str | list[str] | tuple[str, ...]) -> None:
        if key not in self.ALLOWED_KEYS:
            raise ValueError("Tipo de contexto operacional não permitido.")
        if isinstance(value, str):
            stored: ContextValue = value
        else:
            stored = tuple(value)
        self._values[key] = stored

    def get(self, key: str) -> ContextValue | None:
        if key not in self.ALLOWED_KEYS:
            return None
        return self._values.get(key)

    def clear(self) -> None:
        self._values.clear()
