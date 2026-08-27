from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal


@dataclass(frozen=True)
class ObsidianCommand:
    """A conservative deterministic command for common Obsidian actions."""

    action: Literal["create", "append", "open", "read", "open_read", "list"]
    reference: str | None = None
    content: str = ""


class ObsidianCommandParser:
    """Parse high-confidence note commands without asking the LLM to act.

    The parser accepts common Brazilian Portuguese voice variations, but only
    returns a command when the requested operation and its data are clear.
    Ambiguous requests fall back to the agent loop or a clarification.
    """

    _CREATE = re.compile(
        r"\b(?:crie|cria|criar|faca|faça|faz|fazer|monte|monta)\b", re.I
    )
    _CREATE_STT_PAST = re.compile(r"\bcriou\b", re.I)
    _NOTE = re.compile(r"\b(?:nota|anotacao|anotação)\b", re.I)
    _WRITE = re.compile(
        r"\b(?:escreva|escreve|escrever|coloque|coloca|colocar|"
        r"anote|anota|anotar|adicione|adiciona|adicionar|acrescente|"
        r"bote|bota|botar|ponha|poe|põe)\b",
        re.I,
    )
    _OPEN = re.compile(r"\b(?:abra|abre|abrir)\b", re.I)
    _READ = re.compile(r"\b(?:leia|le|lê|ler)\b", re.I)
    _LIST = re.compile(
        r"\b(?:liste|lista|listar|quais|mostre|mostrar|diga|fala|fale)\b"
        r".*\b(?:notas?|anota(?:ção|ções|cao|coes))\b",
        re.I,
    )
    _LIST_CONTEXT = re.compile(
        r"\b(?:liste|lista|listar|mostre|mostrar)\b.*\b(?:elas|todas)\b",
        re.I,
    )
    _CONTEXT_REFERENCE = re.compile(
        r"\b(?:essa nota|esta nota|a nota|nela|dela|ela|essa|esta|"
        r"a anterior|nota anterior)\b",
        re.I,
    )

    _TITLE_PATTERNS = (
        re.compile(
            r"\b(?:nota|anotacao|anotação)\s+(?:chamada|chamado)\s+"
            r"(?P<title>.+?)(?=\s*(?:(?:,|;|:|-)\s*|\be\s+)(?:no\s+texto|"
            r"(?:a|no|na|dentro)?\s*(?:escreva|escreve|coloque|coloca|anote|"
            r"anota|adicione|adiciona|bote|bota|ponha|poe|põe))\b|$)",
            re.I,
        ),
        re.compile(
            r"\b(?:com\s+(?:o\s+)?)?t[ií]tulo\s+(?:como\s+|de\s+)?"
            r"(?P<title>.+?)(?=\s*(?:(?:,|;|:|-)\s*|\be\s+)(?:no\s+texto|"
            r"(?:a|no|na|dentro)?\s*(?:escreva|escreve|coloque|coloca|anote|"
            r"anota|adicione|adiciona|bote|bota|ponha|poe|põe))\b|$)",
            re.I,
        ),
    )

    _CREATE_CONTENT_PATTERNS = (
        re.compile(
            r"(?:\be\s+|[,;:]\s*)(?:no\s+texto\s+(?:dentro\s+dela\s+)?"
            r"(?:diz|diga)\s+que)\s+(?P<content>.+)$",
            re.I,
        ),
        re.compile(
            r"(?:\be\s+|[,;:]\s*)(?:escreva|escreve|coloque|coloca|anote|"
            r"anota|adicione|adiciona|bote|bota|ponha|poe|põe)"
            r"(?:\s+(?:ai|aí))?(?:\s+(?:nela|dentro\s+dela))?"
            r"\s*[,.:;-]*\s*(?P<content>.+)$",
            re.I,
        ),
    )

    _NESTED_APPEND = re.compile(
        r"\b(?:anote|anota|escreva|escreve|adicione|adiciona|coloque|coloca|"
        r"bote|bota|ponha|poe|põe)\s+(?:em|na|nesta|nessa)\s+"
        r"(?:a\s+)?(?:nota\s+)?(?P<reference>.+?)\s+"
        r"(?:dentro\s+dela\s+|nela\s+)?"
        r"(?:coloque|coloca|escreva|escreve|anote|anota|adicione|adiciona|"
        r"bote|bota|ponha|poe|põe)\s+(?P<content>.+)$",
        re.I,
    )
    _PREFIXED_APPEND = re.compile(
        r"\b(?:dentro\s+de|dentro\s+da|na|nesta|nessa|em)\s+"
        r"(?:a\s+)?(?:nota\s+)?(?P<reference>.+?)\s+"
        r"(?:coloque|coloca|escreva|escreve|anote|anota|adicione|adiciona|"
        r"bote|bota|ponha|poe|põe)\s+(?P<content>.+)$",
        re.I,
    )
    _MD_TARGET_APPEND = re.compile(
        r"\b(?:anote|anota|escreva|escreve|adicione|adiciona|coloque|coloca|"
        r"bote|bota|ponha|poe|põe)\s+(?:em|na|nesta|nessa)\s+"
        r"(?:a\s+)?(?:nota\s+)?(?P<reference>.+?\.md)\s+"
        r"(?P<content>.+)$",
        re.I,
    )
    _FOLLOW_UP_READ = re.compile(
        r"\s+(?:e\s+)?(?:depois\s+)?(?:leia|le|lê|ler|mostre|mostrar)\b.*$",
        re.I,
    )

    _REFERENCE_CLEANUP = re.compile(
        r"\b(?:pra|para)\s+mim\b|\bno\s+obsidian\b|\bpor\s+favor\b",
        re.I,
    )

    def parse(self, message: str) -> ObsidianCommand | None:
        value = " ".join(message.strip().split())
        if not value:
            return None

        if self._LIST.search(value) or self._LIST_CONTEXT.search(value):
            return ObsidianCommand("list")

        create = self._parse_create(value)
        if create is not None:
            return create

        # Write-before-read matters for phrases such as
        # "coloque X e depois leia", where read is a compound follow-up.
        append = self._parse_append(value)
        if append is not None:
            return append

        has_open = self._OPEN.search(value) is not None
        has_read = self._READ.search(value) is not None
        if has_open or has_read:
            reference = self._extract_reference(
                value, has_open=has_open, has_read=has_read
            )
            if has_open and has_read:
                return ObsidianCommand("open_read", reference=reference)
            if has_open:
                return ObsidianCommand("open", reference=reference)
            return ObsidianCommand("read", reference=reference)
        return None

    def looks_like_action(self, message: str) -> bool:
        value = self.normalize(message)
        return bool(
            re.search(
                r"\b(?:abra|abre|abrir|leia|le|ler|crie|cria|"
                r"criar|escreva|escreve|anote|anota|adicione|adiciona|"
                r"coloque|coloca|bote|bota|ponha|poe|apague|apaga|exclua|"
                r"excluir|liste|lista)\b",
                value,
            )
        )

    def looks_like_create_without_title(self, message: str) -> bool:
        value = " ".join(message.strip().split())
        return bool(self._CREATE.search(value) and self._NOTE.search(value)) and (
            self._parse_create(value) is None
        )

    def _parse_create(self, value: str) -> ObsidianCommand | None:
        explicit_create = self._CREATE.search(value) is not None
        stt_past_create = self._CREATE_STT_PAST.search(value) is not None
        if (not explicit_create and not stt_past_create) or self._NOTE.search(value) is None:
            return None
        title: str | None = None
        for pattern in self._TITLE_PATTERNS:
            match = pattern.search(value)
            if match is not None:
                title = self._clean_reference(match.group("title"))
                break
        if not title:
            return None

        content = ""
        for pattern in self._CREATE_CONTENT_PATTERNS:
            match = pattern.search(value)
            if match is not None:
                content = self._clean_content(match.group("content"))
                break
        if stt_past_create and not explicit_create and not content:
            return None
        return ObsidianCommand("create", reference=title, content=content)

    def _parse_append(self, value: str) -> ObsidianCommand | None:
        if self._CREATE.search(value) is not None and self._NOTE.search(value) is not None:
            return None

        for pattern in (
            self._NESTED_APPEND,
            self._PREFIXED_APPEND,
            self._MD_TARGET_APPEND,
        ):
            match = pattern.search(value)
            if match is None:
                continue
            reference = self._clean_reference(match.group("reference"))
            content = self._trim_follow_up(self._clean_content(match.group("content")))
            if reference and content:
                return ObsidianCommand(
                    "append", reference=reference, content=content
                )

        match = self._WRITE.search(value)
        if match is None:
            return None
        tail = value[match.end():]
        tail = re.sub(
            r"^\s*(?:(?:pra|para)\s+mim\s*)?(?:,\s*)?"
            r"(?:(?:na|nessa|nesta|dentro\s+dessa|dentro\s+desta)\s+nota|"
            r"nela|dentro\s+dela)?\s*[,.:;-]*\s*",
            "",
            tail,
            flags=re.I,
        )
        content = self._trim_follow_up(self._clean_content(tail))
        if content.endswith("."):
            content = content[:-1].rstrip()
        if not content:
            return None
        return ObsidianCommand("append", reference=None, content=content)

    def _extract_reference(
        self, value: str, *, has_open: bool, has_read: bool
    ) -> str | None:
        if self._CONTEXT_REFERENCE.search(value):
            concrete = self._explicit_reference(
                value, has_open=has_open, has_read=has_read
            )
            return concrete
        return self._explicit_reference(
            value, has_open=has_open, has_read=has_read
        )

    def _explicit_reference(
        self, value: str, *, has_open: bool, has_read: bool
    ) -> str | None:
        pattern = self._OPEN if has_open else self._READ
        match = pattern.search(value)
        if match is None:
            return None
        tail = value[match.end():]
        tail = re.split(
            r"\s+e\s+(?:depois\s+)?(?:leia|le|lê|ler|mostre|mostrar)\b",
            tail,
            maxsplit=1,
            flags=re.I,
        )[0]
        tail = re.sub(
            r"^\s*(?:(?:pra|para)\s+(?:mim|minha)\s*)?"
            r"(?:a\s+|o\s+)?(?:minha\s+)?nota\s*[,.:;-]*\s*",
            "",
            tail,
            flags=re.I,
        )
        tail = re.sub(
            r"^\s*(?:(?:pra|para)\s+mim\s*)?"
            r"(?:essa|esta|a)\s+nota\b.*$",
            "",
            tail,
            flags=re.I,
        )
        # "leia a Jarvis Tool" -> "Jarvis Tool".
        tail = re.sub(r"^\s*(?:a|o)\s+(?=\S)", "", tail, flags=re.I)
        tail = self._clean_reference(tail)
        if not tail or self.normalize(tail) in {
            "ela", "essa", "esta", "essa nota", "esta nota", "a nota",
            "nota", "a anterior", "anterior",
        }:
            return None
        return tail

    def _clean_reference(self, value: str) -> str:
        cleaned = self._REFERENCE_CLEANUP.sub(" ", value)
        cleaned = re.sub(r"^[\s,.:;?!\"']+|[\s,.:;?!\"']+$", "", cleaned)
        return " ".join(cleaned.split())

    @classmethod
    def _trim_follow_up(cls, value: str) -> str:
        return cls._FOLLOW_UP_READ.sub("", value).strip(" ,.;:-")

    @staticmethod
    def _clean_content(value: str) -> str:
        cleaned = value.strip().strip(' "').strip()
        cleaned = re.sub(
            r"\s+(?:dentro\s+(?:dele|dela)|nele|nela)\.?$",
            "",
            cleaned,
            flags=re.I,
        )
        return cleaned.strip()

    @staticmethod
    def normalize(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
