from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any


@dataclass(frozen=True)
class PlannedToolCall:
    name: str
    arguments: dict[str, Any]


class CompoundActionPlanner:
    """Planeja apenas continuações determinísticas e conservadoras."""

    _READ_PATTERN = re.compile(
        r"\b(?:leia|ler|le|lê|leia-a|leia ela|le ela|ler ela|"
        r"mostre|mostrar|me diga o que (?:tem|esta escrito))\b"
    )
    _NEGATED_READ_PATTERN = re.compile(
        r"\b(?:nao leia|nao ler|sem ler|nao precisa ler)\b"
    )
    _READABLE_PREDECESSORS = {
        "obsidian_open_note",
        "obsidian_append_note",
        "obsidian_create_note",
    }

    _OPEN_READ_PATTERN = re.compile(
        r"\b(?:abra|abrir)\s+(?:a\s+|o\s+)?(?:nota\s+)?"
        r"(?P<reference>.+?)\s+(?:e\s+depois\s+|e\s+|depois\s+)"
        r"(?:leia|ler|le)\b"
    )

    def explicit_open_read_reference(self, message: str) -> str | None:
        """Extract a note reference only from an explicit open+read request."""
        match = self._OPEN_READ_PATTERN.search(self.normalize(message))
        if match is None:
            return None
        reference = " ".join(match.group("reference").split())
        return reference or None

    def follow_up(
        self,
        message: str,
        *,
        completed_tool: str,
        completed_arguments: dict[str, Any],
        already_requested: set[str] | None = None,
    ) -> PlannedToolCall | None:
        if completed_tool not in self._READABLE_PREDECESSORS:
            return None
        if already_requested and "obsidian_read_note" in already_requested:
            return None
        path = completed_arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return None
        normalized = self.normalize(message)
        if self._NEGATED_READ_PATTERN.search(normalized):
            return None
        if self._READ_PATTERN.search(normalized) is None:
            return None
        return PlannedToolCall("obsidian_read_note", {"path": path})

    @staticmethod
    def normalize(message: str) -> str:
        value = unicodedata.normalize("NFKD", message.casefold())
        value = "".join(char for char in value if not unicodedata.combining(char))
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return " ".join(value.split())
