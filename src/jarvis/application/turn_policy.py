"""Classificação determinística entre conversa consultiva e efeitos reais."""

from enum import Enum
import re
import unicodedata


class TurnKind(Enum):
    ADVISORY = "advisory"
    EFFECT_REQUEST = "effect_request"


class TurnPolicy:
    """Reconhece somente sinais fortes de opinião, hipótese e planejamento."""

    _ADVISORY = re.compile(
        r"\b(?:o que (?:voce )?acha|qual (?:e )?(?:a )?sua opiniao|"
        r"na sua opiniao|quero sua opiniao|gostaria da sua opiniao|"
        r"voce acha que|voce recomenda|vale a pena|seria uma boa ideia|"
        r"me ajude a planejar|vamos planejar|vamos pensar|"
        r"quais seriam os passos|como eu poderia|como poderiamos|"
        r"avalie se|analise se|compare |o que seria melhor|"
        r"qual seria melhor|o que e melhor|qual e melhor|"
        r"seria melhor|devo )\b"
    )
    _EXPLICIT_FOLLOWUP_EFFECT = re.compile(
        r"\b(?:e|depois|em seguida)\s+(?:abra|crie|edite|apague|exclua|"
        r"mova|execute|rode|foque|inicie|adicione|escreva|anote|faca|faz|"
        r"coloque)\b"
    )

    def classify(self, message: str) -> TurnKind:
        normalized = self.normalize(message)
        if (
            self._EXPLICIT_FOLLOWUP_EFFECT.search(normalized)
            or self._has_sentence_effect(message)
        ):
            return TurnKind.EFFECT_REQUEST
        if self._ADVISORY.search(normalized):
            return TurnKind.ADVISORY
        return TurnKind.EFFECT_REQUEST

    @classmethod
    def _has_sentence_effect(cls, message: str) -> bool:
        decomposed = unicodedata.normalize("NFKD", message.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return re.search(
            r"[.!?;]\s*(?:por favor\s+)?(?:abra|crie|edite|apague|exclua|"
            r"mova|execute|rode|foque|inicie|adicione|escreva|anote|faca|"
            r"coloque)\b",
            plain,
        ) is not None

    @staticmethod
    def normalize(message: str) -> str:
        decomposed = unicodedata.normalize("NFKD", message.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
