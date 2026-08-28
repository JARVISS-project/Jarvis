"""Deterministic intent checks for destructive tool requests."""

from dataclasses import dataclass
import re
from typing import Any

from jarvis.security.risk import RiskLevel
from jarvis.security.confirmation_intent import ConfirmationInterpreter
from jarvis.tools.base import Tool


@dataclass(frozen=True)
class ToolIntentDecision:
    allowed: bool
    response: str | None = None
    requires_intent_confirmation: bool = False


class ToolIntentGuard:
    """Require destructive targets to be explicit in the user's own message."""

    _GENERIC_TARGETS = frozenset(
        {
            "algum arquivo",
            "alguma coisa",
            "arquivo",
            "coisa",
            "elemento",
            "item",
            "qualquer arquivo",
            "um arquivo",
        }
    )
    _TARGET_ARGUMENTS = ("path", "target", "source")
    _CONTEXTUAL_TARGETS = frozenset(
        {
            "ele", "ela", "esse", "essa", "isso", "esse arquivo",
            "essa nota", "aquele arquivo", "aquela nota", "a primeira",
            "a segunda", "a terceira", "a ultima", "o primeiro",
            "o segundo", "o terceiro", "o ultimo",
        }
    )
    _HYPOTHETICAL_PATTERNS = (
        r"\be\s+se\s+eu\s+(?:quero|quiser)\b",
        r"\bvoce\s+poderia\b",
        r"\bcomo\s+(?:eu\s+)?faria\b",
        r"\bo\s+que\s+acontece\s+se\b",
        r"\bseria\s+possivel\b",
    )

    def validate(
        self,
        user_message: str,
        tool: Tool,
        arguments: dict[str, Any],
    ) -> ToolIntentDecision:
        if tool.risk < RiskLevel.HIGH:
            return ToolIntentDecision(allowed=True)

        targets = [
            arguments.get(name)
            for name in self._TARGET_ARGUMENTS
            if isinstance(arguments.get(name), str)
        ]
        normalized_message = self._normalize(user_message)
        if targets and all(
            self._normalize(target).strip(" \"'") in self._CONTEXTUAL_TARGETS
            for target in targets
        ):
            return ToolIntentDecision(allowed=True)
        if any(
            re.search(pattern, normalized_message)
            for pattern in self._HYPOTHETICAL_PATTERNS
        ):
            target = targets[0] if targets else "esse alvo"
            return ToolIntentDecision(
                allowed=False,
                response=f"Você quer que eu realmente exclua {target}?",
                requires_intent_confirmation=True,
            )
        if not targets or not any(
            self._is_explicit(user_message, target) for target in targets
        ):
            response = (
                "Qual arquivo você deseja apagar?"
                if tool.name == "delete_file"
                else "Qual alvo você deseja usar nessa ação?"
            )
            return ToolIntentDecision(allowed=False, response=response)

        return ToolIntentDecision(allowed=True)

    def _is_explicit(self, user_message: str, target: str) -> bool:
        normalized_target = self._normalize(target).strip(" \"'")
        if not normalized_target or normalized_target in self._GENERIC_TARGETS:
            return False

        normalized_message = self._normalize(user_message)
        pattern = rf"(?<!\w){re.escape(normalized_target).replace(r'\ ', ' ')}(?!\w)"
        return re.search(pattern, normalized_message) is not None

    @staticmethod
    def _normalize(value: str) -> str:
        return ConfirmationInterpreter.normalize(value).replace("\\", "/")
