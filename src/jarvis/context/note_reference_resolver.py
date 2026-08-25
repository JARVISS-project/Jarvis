"""Resolução determinística de referências a notas do Vault."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
import re
import unicodedata

from jarvis.context.recent import RecentContext
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.speech.normalizer import TranscriptNormalizer


class NoteMatchKind(Enum):
    EXACT_LITERAL = "exact_literal"
    NORMALIZED = "normalized"
    CONTEXT = "context"
    FUZZY = "fuzzy"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class NoteReferenceMatch:
    kind: NoteMatchKind
    canonical_path: str | None = None
    candidates: tuple[str, ...] = ()
    score: float | None = None


class NoteReferenceResolver:
    FUZZY_THRESHOLD = 0.68
    AMBIGUITY_MARGIN = 0.05
    MAX_CANDIDATES = 3
    ORDINALS = {
        "primeira": 0, "primeiro": 0,
        "segunda": 1, "segundo": 1,
        "terceira": 2, "terceiro": 2,
        "ultima": -1, "ultimo": -1,
    }
    CONTEXT_WORDS = frozenset(
        {
            "ela", "ele", "essa", "essa nota", "aquela nota", "a nota",
            "o assunto dela", "aquela que voce abriu",
            "a nota que voce acabou de ler", "a nota anterior", "anterior",
        }
    )

    def __init__(
        self,
        vault: ObsidianVault,
        recent_context: RecentContext,
    ) -> None:
        self.vault = vault
        self.recent_context = recent_context
        self.normalizer = TranscriptNormalizer()

    def resolve(self, reference: str) -> NoteReferenceMatch:
        value = reference.strip()
        if Path(value).suffix.casefold() == ".md":
            literal = self.vault.resolve_note(value)
            if literal.is_file():
                path = literal.relative_to(self.vault.vault_path).as_posix()
                return NoteReferenceMatch(
                    NoteMatchKind.EXACT_LITERAL, path, (path,), 1.0
                )

        normalized_words = self._words(value)
        recent = self._recent_results()
        ordinal = self._ordinal(normalized_words)
        if ordinal is not None and recent and ordinal < len(recent):
            return NoteReferenceMatch(
                NoteMatchKind.CONTEXT,
                recent[ordinal],
                (recent[ordinal],),
                1.0,
            )
        if normalized_words in self.CONTEXT_WORDS:
            last_note = self.recent_context.get("last_active_note")
            if not isinstance(last_note, str):
                last_note = self.recent_context.get("last_note")
            if isinstance(last_note, str):
                target = self.vault.resolve_note(last_note)
                if target.is_file():
                    return NoteReferenceMatch(
                        NoteMatchKind.CONTEXT,
                        last_note,
                        (last_note,),
                        1.0,
                    )

        normalized_path = self.normalizer.normalize(value)
        requested = self.vault.resolve_note(
            normalized_path
            if Path(normalized_path).suffix.casefold() == ".md"
            else f"{normalized_path}.md"
        )
        if requested.is_file():
            path = requested.relative_to(self.vault.vault_path).as_posix()
            return NoteReferenceMatch(
                NoteMatchKind.NORMALIZED, path, (path,), 1.0
            )

        notes = self.vault.list_notes()
        ordered = list(dict.fromkeys([*recent, *notes]))
        query = self._comparison(value)
        scored = sorted(
            (
                (self._score(query, path), path)
                for path in ordered
            ),
            key=lambda item: (-item[0], item[1].casefold()),
        )
        likely = [item for item in scored if item[0] >= self.FUZZY_THRESHOLD]
        if not likely:
            return NoteReferenceMatch(NoteMatchKind.NOT_FOUND)
        if (
            len(likely) > 1
            and likely[0][0] - likely[1][0] <= self.AMBIGUITY_MARGIN
        ):
            choices = tuple(
                path for _, path in likely[: self.MAX_CANDIDATES]
            )
            return NoteReferenceMatch(
                NoteMatchKind.AMBIGUOUS, candidates=choices
            )
        score, path = likely[0]
        return NoteReferenceMatch(
            NoteMatchKind.FUZZY, path, (path,), score
        )

    def _recent_results(self) -> tuple[str, ...]:
        results = self.recent_context.get("last_note_results")
        return results if isinstance(results, tuple) else ()

    def _ordinal(self, value: str) -> int | None:
        for word, index in self.ORDINALS.items():
            if re.search(rf"\b{word}\b", value):
                return index
        return None

    @classmethod
    def _score(cls, query: str, path: str) -> float:
        full = cls._comparison(path)
        stem = cls._comparison(Path(path).stem)
        return max(
            SequenceMatcher(None, query, full).ratio(),
            SequenceMatcher(None, query, stem).ratio(),
        )

    @classmethod
    def _comparison(cls, value: str) -> str:
        words = cls._words(value)
        words = re.sub(
            r"\b(?:abra|abrir|leia|ler|nota|minha|sobre|a|o|de|do|da)\b",
            " ",
            words,
        )
        return "".join(re.findall(r"[a-z0-9]+", words))

    @staticmethod
    def _words(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
