from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass
class ResponseVariationManager:
    """Small deterministic phrase rotation for natural voice interactions.

    These phrases are intentionally code-owned instead of LLM-generated. Wake
    acknowledgements need to be immediate, predictable and safe, and they must
    not consume a model call just to say that Jarvis is listening.
    """

    wake_phrases: tuple[str, ...] = (
        "Oi, tô aqui.",
        "Diga.",
        "Estou ouvindo.",
        "Pode falar.",
        "Sim?",
    )
    action_phrases: tuple[str, ...] = (
        "Certo.",
        "Pode deixar.",
        "Já vejo isso.",
        "Beleza.",
    )
    _wake_index: int = field(default=0, init=False, repr=False)
    _action_index: int = field(default=0, init=False, repr=False)

    _ACTION = re.compile(
        r"\b(?:abra|abre|abrir|crie|cria|criar|criou|escreva|escreve|"
        r"anote|anota|adicione|adiciona|leia|lê|le|ler|liste|lista|"
        r"execute|executa|rode|roda|salve|memorize|aprenda|aprende)\b",
        re.IGNORECASE,
    )

    def wake(self) -> str:
        return self._next("wake")

    def action_acknowledgement(self, text: str) -> str | None:
        """Return a short acknowledgement only for clear action requests.

        Questions and explanations should go straight to the answer; otherwise
        the assistant becomes chatty ("Certo" + answer) on every turn.
        """
        if self._ACTION.search(text) is None:
            return None
        return self._next("action")

    def _next(self, group: str) -> str:
        if group == "wake":
            if not self.wake_phrases:
                return ""
            value = self.wake_phrases[self._wake_index % len(self.wake_phrases)]
            self._wake_index += 1
            return value
        if not self.action_phrases:
            return ""
        value = self.action_phrases[
            self._action_index % len(self.action_phrases)
        ]
        self._action_index += 1
        return value
