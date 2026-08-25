"""Heurística local para detectar finais de fala provavelmente incompletos."""

import re
import unicodedata


class IncompleteUtteranceDetector:
    INCOMPLETE_ENDINGS = (
        "eu queria saber",
        "eu gostaria de",
        "nesse caso",
        "entao",
        "porque",
        "por que",
        "e",
        "mas",
        "agora eu queria",
        "eu queria que voce",
        "o que acontece se",
        "e sobre",
        "e aquela",
        "e aquele",
    )

    def is_possibly_incomplete(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        normalized = self._normalize(stripped)
        if not normalized:
            return False
        if stripped.endswith("?") and not stripped.endswith("...?"):
            return False
        return stripped.endswith(("...", "…")) or any(
            normalized == ending or normalized.endswith(f" {ending}")
            for ending in self.INCOMPLETE_ENDINGS
        )

    @staticmethod
    def _normalize(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
