"""Resolução determinística e confinada de referências a arquivos."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
import re
import unicodedata

from jarvis.context.recent import RecentContext
from jarvis.security.filesystem import FileSystemGuard
from jarvis.speech.normalizer import TranscriptNormalizer


class FileMatchKind(Enum):
    EXACT_LITERAL = "exact_literal"
    EXACT = "exact_literal"
    NORMALIZED = "normalized"
    FUZZY = "fuzzy"
    CONTEXT = "context"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class FileReferenceMatch:
    kind: FileMatchKind
    path: str | None = None
    candidates: tuple[str, ...] = ()
    score: float | None = None

    @property
    def canonical_path(self) -> str | None:
        return self.path

    @property
    def match_type(self) -> FileMatchKind:
        return self.kind


class FileReferenceResolver:
    MAX_CANDIDATES = 3
    FUZZY_THRESHOLD = 0.68
    AMBIGUITY_MARGIN = 0.05
    RECENT_BONUS = 0.06
    ORDINALS = {
        "primeira": 0, "primeiro": 0,
        "segunda": 1, "segundo": 1,
        "terceira": 2, "terceiro": 2,
        "ultima": -1, "ultimo": -1,
    }
    CONTEXT_REFERENCES = frozenset(
        {
            "ele", "ela", "isso", "esse", "esse arquivo", "aquele arquivo",
            "o arquivo", "o assunto dele", "aquele que voce abriu",
            "o arquivo que voce acabou de ler", "anterior",
        }
    )

    def __init__(
        self,
        filesystem: FileSystemGuard,
        recent_context: RecentContext | None = None,
    ) -> None:
        self.filesystem = filesystem
        self.recent_context = recent_context or RecentContext()
        self.transcript_normalizer = TranscriptNormalizer()

    def resolve(self, spoken_path: str) -> FileReferenceMatch:
        literal = self.filesystem.resolve(spoken_path.strip())
        if literal.is_file():
            return FileReferenceMatch(
                FileMatchKind.EXACT_LITERAL,
                literal.relative_to(self.filesystem.base_directory).as_posix(),
                score=1.0,
            )
        normalized_spoken = self.transcript_normalizer.normalize(
            spoken_path.strip()
        )
        recent_results = self._recent_search_results()
        normalized_words = self._unaccent(normalized_spoken)
        for word, index in self.ORDINALS.items():
            if re.search(rf"\b{word}\b", normalized_words):
                if recent_results and index < len(recent_results):
                    target = self.filesystem.resolve(recent_results[index])
                    if target.is_file():
                        return FileReferenceMatch(
                            FileMatchKind.CONTEXT,
                            recent_results[index],
                            (recent_results[index],),
                            1.0,
                        )
                break
        if self._comparison_form(normalized_spoken) in self.CONTEXT_REFERENCES:
            recent = self.recent_context.get("last_active_file")
            if not isinstance(recent, str):
                recent = self.recent_context.get("last_file")
            if isinstance(recent, str):
                target = self.filesystem.resolve(recent)
                if target.is_file():
                    return FileReferenceMatch(
                        FileMatchKind.CONTEXT, recent, score=1.0
                    )

        requested = self.filesystem.resolve(normalized_spoken)
        if requested.is_file():
            return FileReferenceMatch(
                FileMatchKind.NORMALIZED,
                requested.relative_to(self.filesystem.base_directory).as_posix(),
                score=1.0,
            )

        parent_text = Path(normalized_spoken).parent.as_posix()
        parent = self.filesystem.resolve(parent_text)
        if not parent.is_dir():
            return FileReferenceMatch(FileMatchKind.NOT_FOUND)
        candidates: list[Path] = []
        recent_paths = self._recent_file_paths()
        for relative in recent_paths:
            try:
                guarded = self.filesystem.resolve(relative)
            except PermissionError:
                continue
            if guarded.is_file() and guarded.parent == parent:
                candidates.append(guarded)
        for entry in parent.iterdir():
            relative = entry.relative_to(self.filesystem.base_directory).as_posix()
            try:
                guarded = self.filesystem.resolve(relative)
            except PermissionError:
                continue
            if guarded.is_file() and guarded not in candidates:
                candidates.append(guarded)
        spoken_name = Path(normalized_spoken).name
        comparison = self._comparison_form(spoken_name)
        normalized_matches = [
            entry for entry in candidates
            if self._comparison_form(entry.name) == comparison
        ]
        if len(normalized_matches) == 1:
            return self._match(
                FileMatchKind.NORMALIZED, normalized_matches[0], 1.0
            )
        if len(normalized_matches) > 1:
            return self._ambiguous(normalized_matches)

        spoken_forms = self._spoken_forms(spoken_name)
        scored = sorted(
            [
                (
                    min(
                        1.0,
                        self._candidate_score(spoken_forms, entry.name)
                        + (
                            self.RECENT_BONUS
                            if self._relative(entry) in recent_paths
                            else 0.0
                        ),
                    ),
                    entry,
                )
                for entry in candidates
            ],
            key=lambda item: (-item[0], self._relative(item[1])),
        )
        likely = [item for item in scored if item[0] >= self.FUZZY_THRESHOLD]
        if not likely:
            return FileReferenceMatch(FileMatchKind.NOT_FOUND)
        if len(likely) > 1 and likely[0][0] - likely[1][0] <= self.AMBIGUITY_MARGIN:
            return self._ambiguous(
                [entry for _, entry in likely[: self.MAX_CANDIDATES]]
            )
        return self._match(FileMatchKind.FUZZY, likely[0][1], likely[0][0])

    def _match(
        self,
        kind: FileMatchKind,
        target: Path,
        score: float | None = None,
    ) -> FileReferenceMatch:
        relative = self._relative(target)
        return FileReferenceMatch(kind, relative, (relative,), score)

    def _ambiguous(self, targets: list[Path]) -> FileReferenceMatch:
        candidates = tuple(
            target.relative_to(self.filesystem.base_directory).as_posix()
            for target in targets[: self.MAX_CANDIDATES]
        )
        return FileReferenceMatch(FileMatchKind.AMBIGUOUS, candidates=candidates)

    def _recent_file_paths(self) -> tuple[str, ...]:
        values: list[str] = []
        values.extend(self._recent_search_results())
        last_file = self.recent_context.get("last_file")
        if isinstance(last_file, str):
            values.insert(0, last_file)
        return tuple(dict.fromkeys(values))

    def _recent_search_results(self) -> tuple[str, ...]:
        searches = self.recent_context.get("last_file_results")
        if not isinstance(searches, tuple):
            searches = self.recent_context.get("last_search_results")
        return searches if isinstance(searches, tuple) else ()

    def _relative(self, target: Path) -> str:
        return target.relative_to(self.filesystem.base_directory).as_posix()

    @classmethod
    def _candidate_score(cls, spoken_forms: set[str], filename: str) -> float:
        candidate_forms = cls._filename_aliases(filename)
        best = 0.0
        for spoken in spoken_forms:
            spoken_tokens = set(re.findall(r"[a-z0-9]+", spoken))
            for candidate in candidate_forms:
                lexical = SequenceMatcher(None, spoken, candidate).ratio()
                candidate_tokens = set(re.findall(r"[a-z0-9]+", candidate))
                union = spoken_tokens | candidate_tokens
                token_score = (
                    len(spoken_tokens & candidate_tokens) / len(union)
                    if union else 0.0
                )
                best = max(best, lexical, (lexical + token_score) / 2)
        return best

    @classmethod
    def _spoken_forms(cls, value: str) -> set[str]:
        normalized = cls._unaccent(value)
        normalized = re.sub(
            r"\b(?:apaga|apague|apagar|delete|deleta|exclua|excluir)\b",
            " ",
            normalized,
        )
        normalized = re.sub(r"\b(?:o|a|um|uma|arquivo|pra|para|mim)\b", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        forms = {cls._comparison_form(normalized)}
        # Um token desconhecido depois de "ponto" pode ser erro de STT da
        # extensão. Removê-lo só para pontuar candidatos, nunca para executar.
        without_spoken_extension = re.sub(r"\bponto\s+\w+\s*$", "", normalized)
        forms.add(cls._comparison_form(without_spoken_extension))
        forms.add(re.sub(r"[^a-z0-9]", "", without_spoken_extension))
        return {form for form in forms if form}

    @classmethod
    def _filename_aliases(cls, filename: str) -> set[str]:
        path = Path(cls._unaccent(filename))
        stem = re.sub(r"[_-]+", " ", path.stem)
        extension = path.suffix.lstrip(".")
        compact_stem = cls._comparison_form(stem)
        aliases = {compact_stem, cls._comparison_form(filename)}
        if extension:
            aliases.add(f"{compact_stem}{extension}")
            aliases.add(f"{compact_stem}.{extension}")
        return aliases

    @staticmethod
    def _unaccent(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        return "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )

    @staticmethod
    def _comparison_form(value: str) -> str:
        unaccented = FileReferenceResolver._unaccent(value)
        expanded = re.sub(
            r"\b(?:underline|underscore|sublinhado)\b", "_", unaccented
        )
        expanded = re.sub(r"\bhifen\b", "-", expanded)
        stem, suffix = Path(expanded).stem, Path(expanded).suffix
        tokens = re.findall(r"[a-z0-9]+", stem)
        tokens = [token for token in tokens if token not in {"de", "da", "do"}]
        extension = re.sub(r"[^a-z0-9]", "", suffix)
        return "".join(tokens) + (f".{extension}" if extension else "")
