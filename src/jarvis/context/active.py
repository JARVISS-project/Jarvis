"""Roteamento local e efêmero da fonte ativa da conversa."""

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata


class ActiveContext(Enum):
    NONE = "NONE"
    FILESYSTEM = "FILESYSTEM"
    OBSIDIAN = "OBSIDIAN"


@dataclass
class ConversationContext:
    active: ActiveContext = ActiveContext.NONE


class ContextRouter:
    """Escolhe apenas a fonte; nunca autoriza nem executa ações."""

    OBSIDIAN_CORRECTIONS = (
        "nao no diretorio no obsidian",
        "nao quero ver no diretorio eu quero ver no obsidian",
        "nao quero ver no diretorio quero ver no obsidian",
        "nao e arquivo e nota",
        "eu quis dizer minhas notas",
        "quero no obsidian",
        "procure no obsidian",
    )
    FILESYSTEM_CORRECTIONS = (
        "nao no obsidian no projeto",
        "nao quero no obsidian quero no projeto",
        "quero o arquivo",
        "quero no projeto",
    )
    OBSIDIAN_TERMS = frozenset(
        {"nota", "notas", "anotacao", "anotacoes", "obsidian", "vault", "cofre"}
    )
    FILESYSTEM_TERMS = frozenset(
        {
            "arquivo", "arquivos", "pasta", "diretorio", "repositorio",
            "source", "codigo", "src", "test", "teste", "testes", "tests",
            "projeto",
        }
    )
    OBSIDIAN_CONTEXT_REFERENCES = (
        "arquivo que voce acabou de ler",
        "arquivo que voce acabou de abrir",
    )

    def __init__(self, state: ConversationContext | None = None) -> None:
        self.state = state or ConversationContext()

    def update(self, message: str) -> ActiveContext:
        normalized = self.normalize(message)
        if self._contains_any(normalized, self.FILESYSTEM_CORRECTIONS):
            self.state.active = ActiveContext.FILESYSTEM
            return self.state.active
        if self._contains_any(normalized, self.OBSIDIAN_CORRECTIONS):
            self.state.active = ActiveContext.OBSIDIAN
            return self.state.active
        if (
            self.state.active is ActiveContext.OBSIDIAN
            and self._contains_any(
                normalized, self.OBSIDIAN_CONTEXT_REFERENCES
            )
        ):
            return self.state.active

        words = set(re.findall(r"[a-z0-9]+", normalized))
        note_score = len(words & self.OBSIDIAN_TERMS)
        file_score = len(words & self.FILESYSTEM_TERMS)
        if "minhas notas" in normalized:
            note_score += 3
        if any(
            phrase in normalized
            for phrase in ("arquivos do projeto", "no projeto", "do projeto")
        ):
            file_score += 3
        generic_test_terms = {"test", "teste", "testes", "tests"}
        if (
            (self.state.active is ActiveContext.OBSIDIAN or note_score > 0)
            and (words & self.FILESYSTEM_TERMS) <= generic_test_terms
        ):
            # "teste" is often part of a note title (for example
            # "Teste para Jarvis"). An explicit note word must not be tied
            # with this generic filesystem token.
            file_score = 0
        if note_score > file_score:
            self.state.active = ActiveContext.OBSIDIAN
        elif file_score > note_score:
            self.state.active = ActiveContext.FILESYSTEM
        return self.state.active

    @staticmethod
    def normalize(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())

    @staticmethod
    def _contains_any(value: str, phrases: tuple[str, ...]) -> bool:
        return any(phrase in value for phrase in phrases)
