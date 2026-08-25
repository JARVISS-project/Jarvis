"""Interpretação determinística e contextual de confirmações."""

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata


from jarvis.security.confirmation_request import ConfirmationPurpose


class ConfirmationIntent(Enum):
    CONFIRM = "confirm"
    DENY = "deny"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConfirmationResult:
    target_confirmed: bool = False
    action_authorized: bool = False
    denied: bool = False
    ambiguous: bool = False

    def accepts(self, purpose: ConfirmationPurpose) -> bool:
        if self.denied or self.ambiguous:
            return False
        if purpose is ConfirmationPurpose.TARGET:
            return self.target_confirmed
        return self.action_authorized


class ConfirmationInterpreter:
    DENIAL_PHRASES = (
        "nao autorizo", "nao continue", "nao faca", "nao pode",
        "melhor nao", "deixa pra la", "pode deixar", "cancelar",
        "cancela", "cancele", "deixa", "esquece", "parar", "pare", "para",
    )
    AMBIGUOUS_PHRASES = (
        "acho que sim", "provavelmente", "pode ser", "nao sei",
        "se quiser", "se voce acha", "tanto faz", "voce decide", "talvez",
        "creio que seja",
    )
    ACTION_PHRASES = (
        "beleza pode fazer", "beleza pode continuar", "ok pode fazer",
        "sim pode fazer", "pode continuar", "pode executar", "pode apagar",
        "pode excluir", "pode editar", "pode criar", "pode fazer",
        "manda ver", "ok faca", "confirmado", "autorizado", "confirmar",
        "confirmo", "autorizo", "prossiga", "beleza pode", "faca", "faz",
        "beleza", "vai", "pode", "sim", "ok",
    )
    EXPLICIT_ACTION_PHRASES = (
        "pode continuar", "pode executar", "pode apagar", "pode excluir",
        "pode editar", "pode criar", "pode fazer", "manda ver", "ok faca",
        "autorizado", "autorizo", "prossiga", "faca", "faz", "vai",
    )
    TARGET_PHRASES = (
        "sim e esse mesmo", "sim esse mesmo", "sim e esse", "sim esse",
        "isso mesmo", "e isso", "e esse mesmo", "esse mesmo", "e esse ai",
        "e esse", "exatamente", "correto", "certinho", "isso", "sim",
    )

    def interpret(self, text: str) -> ConfirmationIntent:
        normalized = self.normalize(text)
        if not normalized:
            return ConfirmationIntent.UNKNOWN
        if self._contains_any(normalized, self.DENIAL_PHRASES):
            return ConfirmationIntent.DENY
        if self._contains_any(normalized, self.AMBIGUOUS_PHRASES):
            return ConfirmationIntent.UNKNOWN
        if re.search(r"\bnao\b", normalized):
            return ConfirmationIntent.DENY
        if self._contains_any(normalized, self.ACTION_PHRASES):
            return ConfirmationIntent.CONFIRM
        return ConfirmationIntent.UNKNOWN

    def interpret_result(
        self,
        text: str,
        purpose: ConfirmationPurpose | None,
    ) -> ConfirmationResult:
        normalized = self.normalize(text)
        if not normalized:
            return ConfirmationResult(ambiguous=True)
        denied = (
            self._contains_any(normalized, self.DENIAL_PHRASES)
            or re.search(r"\bnao\b", normalized) is not None
        )
        if denied:
            return ConfirmationResult(denied=True)
        if self._contains_any(normalized, self.AMBIGUOUS_PHRASES):
            return ConfirmationResult(ambiguous=True)
        if purpose is None:
            return ConfirmationResult(ambiguous=True)

        target_confirmed = (
            purpose is ConfirmationPurpose.TARGET
            and self._contains_any(normalized, self.TARGET_PHRASES)
        )
        explicit_action = self._contains_any(
            normalized, self.EXPLICIT_ACTION_PHRASES
        )
        action_authorized = (
            explicit_action
            or (
                purpose is ConfirmationPurpose.SECURITY
                and self._contains_any(normalized, self.ACTION_PHRASES)
            )
            or (
                purpose is ConfirmationPurpose.SECURITY
                and self._contains_any(normalized, self.TARGET_PHRASES)
            )
        )
        if purpose is ConfirmationPurpose.TARGET and not target_confirmed:
            return ConfirmationResult(ambiguous=True)
        if purpose is ConfirmationPurpose.SECURITY and not action_authorized:
            return ConfirmationResult(ambiguous=True)
        return ConfirmationResult(
            target_confirmed=target_confirmed,
            action_authorized=action_authorized,
        )

    @staticmethod
    def normalize(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text.casefold())
        unaccented = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        normalized = " ".join(
            re.sub(r"[^\w\s]", " ", unaccented).split()
        )
        return re.sub(r"\bpodi\b", "pode", normalized)

    @staticmethod
    def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
        padded = f" {text} "
        return any(f" {phrase} " in padded for phrase in phrases)
