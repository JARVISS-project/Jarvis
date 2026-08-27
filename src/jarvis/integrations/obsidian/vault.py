"""Operações Markdown de baixo nível dentro de um Vault isolado."""

from collections.abc import Callable
from pathlib import Path
import re
import unicodedata
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

    @staticmethod
    def normalize_note_path(path: str) -> str:
        """Normalize a human note title to a safe relative Markdown path.

        Voice users should not need to pronounce ``ponto md`` every time. A
        missing suffix becomes ``.md``; an explicit non-Markdown suffix still
        fails closed.
        """
        value = path.strip().strip("\"\'")
        if not value:
            raise PermissionError("Informe o nome da nota.")
        candidate = Path(value)
        if candidate.suffix == "":
            value = f"{value}.md"
        elif candidate.suffix.casefold() != ".md":
            raise PermissionError("Somente notas Markdown (.md) são permitidas.")
        return value

    def resolve_note(self, path: str, *, for_write: bool = False) -> Path:
        path = self.normalize_note_path(path)
        if "://" in path:
            raise PermissionError("URIs arbitrárias não são permitidas.")
        if Path(path).is_absolute():
            raise PermissionError("O caminho da nota deve ser relativo ao Vault.")
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
        normalized_query = self._normalize_search_text(query)
        if not normalized_query:
            raise ValueError("A busca não pode ser vazia.")
        variants = self._search_variants(normalized_query)
        results: list[NoteSearchResult] = []
        for path in self.list_notes():
            target = self.resolve_note(path)
            if target.stat().st_size > self.max_note_size_bytes:
                continue
            content = target.read_text(encoding="utf-8")
            normalized_path = self._normalize_search_text(path)
            normalized_content = self._normalize_search_text(content)
            matched_variant = next(
                (
                    variant
                    for variant in variants
                    if variant in normalized_path or variant in normalized_content
                ),
                None,
            )
            if matched_variant is None:
                continue
            path_match = matched_variant in normalized_path
            content_match = matched_variant in normalized_content
            # ``_snippet`` espera um índice do texto original. Para matches
            # normalizados (acentos/plural) pode não existir um índice exato;
            # nesse caso ele usa com segurança o início da nota.
            index = content.casefold().find(matched_variant)
            score = (2 if path_match else 0) + (1 if content_match else 0)
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
        separator = ""
        if target.stat().st_size > 0 and content and not content.startswith("\n"):
            with target.open("rb") as existing:
                existing.seek(-1, 2)
                if existing.read(1) not in {b"\n", b"\r"}:
                    separator = "\n"
        with target.open("a", encoding="utf-8") as note:
            note.write(separator + content)

    def write_note(self, path: str, content: str) -> None:
        """Write an internal Markdown document inside the guarded Vault.

        This method is intentionally not exposed as an LLM Tool. It is used by
        trusted integrations such as the human-readable Skill mirror.
        """
        target = self.resolve_note(path, for_write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def delete_note(self, path: str) -> None:
        self._existing_note(path, for_write=True).unlink()

    def open_note(self, path: str) -> str:
        target = self._existing_note(path)
        uri = build_obsidian_open_uri(target)
        self._uri_opener(uri)
        return uri

    def _existing_note(self, path: str, *, for_write: bool = False) -> Path:
        target = self.resolve_note(path, for_write=for_write)
        if not target.exists():
            raise FileNotFoundError(path)
        if not target.is_file():
            raise IsADirectoryError(path)
        return target


    @staticmethod
    def _normalize_search_text(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())

    @staticmethod
    def _search_variants(normalized_query: str) -> tuple[str, ...]:
        variants = [normalized_query]
        # STT/linguagem natural frequentemente alternam singular/plural
        # ("teste"/"testes"). O fallback é propositalmente conservador:
        # apenas uma palavra final com mais de quatro caracteres perde o "s".
        words = normalized_query.split()
        if words and len(words[-1]) > 4 and words[-1].endswith("s"):
            singular_words = [*words[:-1], words[-1][:-1]]
            singular = " ".join(singular_words)
            if singular and singular != normalized_query:
                variants.append(singular)
        return tuple(variants)

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
