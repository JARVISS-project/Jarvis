"""Deterministic intent checks for destructive tool requests."""

from dataclasses import dataclass
import re
from typing import Any

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


@dataclass(frozen=True)
class ToolIntentDecision:
    allowed: bool
    response: str | None = None


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
        pattern = rf"(?<!\w){re.escape(normalized_target)}(?!\w)"
        return re.search(pattern, normalized_message) is not None

    @staticmethod
    def _normalize(value: str) -> str:
        return value.casefold().replace("\\", "/")
