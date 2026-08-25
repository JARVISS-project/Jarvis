"""Operações Markdown de baixo nível dentro de um Vault isolado."""

from collections.abc import Callable
from pathlib import Path
import webbrowser
from typing import NamedTuple

from jarvis.integrations.obsidian.uri import build_obsidian_open_uri
from jarvis.security.filesystem import FileSystemGuard


class NoteSearchResult(NamedTuple):
    path: str
    snippet: str
    score: int


class ObsidianVault:
    MAX_NOTE_SIZE = 1_000_000
    MAX_SEARCH_RESULTS = 20
    MAX_SNIPPET_LENGTH = 200

    def __init__(
        self,
        vault_path: Path,
        filesystem: FileSystemGuard,
        uri_opener: Callable[[str], object] | None = None,
        max_note_size_bytes: int = MAX_NOTE_SIZE,
        search_max_results: int = 10,
    ) -> None:
        self.vault_path = vault_path.resolve()
        if filesystem.base_directory != self.vault_path:
            raise ValueError("O FileSystemGuard deve ser exclusivo do Vault.")
        self.filesystem = filesystem
        if max_note_size_bytes <= 0 or search_max_results <= 0:
            raise ValueError("Os limites do Vault devem ser positivos.")
        self.max_note_size_bytes = max_note_size_bytes
        self.search_max_results = search_max_results
        self._uri_opener = uri_opener or webbrowser.open

    def resolve_note(self, path: str, *, for_write: bool = False) -> Path:
        if "://" in path:
            raise PermissionError("URIs arbitrárias não são permitidas.")
        if Path(path).is_absolute():
            raise PermissionError("O caminho da nota deve ser relativo ao Vault.")
        if not path or Path(path).suffix.casefold() != ".md":
            raise PermissionError("Somente notas Markdown (.md) são permitidas.")
        target = self.filesystem.resolve(path, for_write=for_write)
        relative = target.relative_to(self.vault_path)
        if any(part.casefold() == ".obsidian" for part in relative.parts):
            raise PermissionError("O diretório .obsidian é protegido.")
        return target

    def list_notes(self) -> list[str]:
        if not self.vault_path.exists():
            raise FileNotFoundError(self.vault_path)
        notes: list[str] = []
        for candidate in self.vault_path.rglob("*"):
            if candidate.suffix.casefold() != ".md":
                continue
            relative = candidate.relative_to(self.vault_path)
            if any(part.startswith(".") for part in relative.parts):
                continue
            try:
                target = self.resolve_note(relative.as_posix())
            except PermissionError:
                continue
            if target.is_file():
                notes.append(relative.as_posix())
        return sorted(notes, key=str.casefold)

    def read_note(self, path: str) -> str:
        target = self._existing_note(path)
        if target.stat().st_size > self.max_note_size_bytes:
            raise ValueError("Nota muito grande para leitura.")
        return target.read_text(encoding="utf-8")

    def search_notes(self, query: str) -> list[NoteSearchResult]:
        normalized_query = query.strip().casefold()
        if not normalized_query:
            raise ValueError("A busca não pode ser vazia.")
        results: list[NoteSearchResult] = []
        for path in self.list_notes():
            target = self.resolve_note(path)
            if target.stat().st_size > self.max_note_size_bytes:
                continue
            content = target.read_text(encoding="utf-8")
            index = content.casefold().find(normalized_query)
            path_match = normalized_query in path.casefold()
            if path_match or index >= 0:
                score = (2 if path_match else 0) + (1 if index >= 0 else 0)
                results.append(
                    NoteSearchResult(path, self._snippet(content, index), score)
                )
        results.sort(key=lambda item: (-item.score, item.path.casefold()))
        return results[: self.search_max_results]

    def create_note(self, path: str, content: str) -> None:
        target = self.resolve_note(path, for_write=True)
        if target.exists():
            raise FileExistsError(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as note:
            note.write(content)

    def append_note(self, path: str, content: str) -> None:
        target = self._existing_note(path, for_write=True)
        with target.open("a", encoding="utf-8") as note:
            note.write(content)

    def delete_note(self, path: str) -> None:
        self._existing_note(path, for_write=True).unlink()

    def open_note(self, path: str) -> str:
        target = self._existing_note(path)
        relative = target.relative_to(self.vault_path).as_posix()
        uri = build_obsidian_open_uri(self.vault_path.name, relative)
        self._uri_opener(uri)
        return uri

    def _existing_note(self, path: str, *, for_write: bool = False) -> Path:
        target = self.resolve_note(path, for_write=for_write)
        if not target.exists():
            raise FileNotFoundError(path)
        if not target.is_file():
            raise IsADirectoryError(path)
        return target

    def _snippet(self, content: str, index: int) -> str:
        compact = " ".join(content.split())
        if not compact:
            return ""
        if index < 0:
            return compact[: self.MAX_SNIPPET_LENGTH]
        query_fragment = content[index:index + self.MAX_SNIPPET_LENGTH // 3]
        compact_index = compact.casefold().find(
            " ".join(query_fragment.split()).casefold()
        )
        if compact_index < 0:
            compact_index = 0
        start = max(0, compact_index - self.MAX_SNIPPET_LENGTH // 3)
        return compact[start:start + self.MAX_SNIPPET_LENGTH]
