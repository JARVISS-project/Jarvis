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
    _EFFECT_INTENT_PATTERNS = {
        "create_file": r"\b(?:crie|cria|criar|gere|gera|gerar)\b",
        "edit_file": r"\b(?:edite|edita|editar|altere|altera|alterar|reescreva|reescrever)\b",
        "move_file": r"\b(?:mova|move|mover|renomeie|renomear)\b",
        "delete_file": r"\b(?:apague|apaga|apagar|delete|deletar|exclua|excluir|remova|remover)\b",
        "obsidian_create_note": r"\b(?:crie|cria|criar|nova nota|anote uma nova)\b",
        "obsidian_append_note": r"\b(?:adicione|adiciona|adicionar|anote|anota|anotar|acrescente|acrescentar|escreva|escreve|escrever|coloque|coloca|colocar|edite|edita|editar)\b",
        "obsidian_delete_note": r"\b(?:apague|apaga|apagar|exclua|excluir|remova|remover)\b",
        "browser_new_tab": r"\b(?:nova aba|abra uma aba|abrir uma aba)\b",
        "browser_close_tab": r"\b(?:feche|fecha|fechar)\b.*\baba\b",
        "browser_switch_tab": r"\b(?:troque|mude|altere|va|ir)\b.*\baba\b",
        "browser_navigate": r"\b(?:navegue|navegar|acesse|acessar|va|ir|abra|abrir)\b",
        "browser_click": r"\b(?:clique|clica|clicar|pressione|pressionar|aperte|apertar)\b",
        "browser_type": r"\b(?:digite|digita|digitar|escreva|escrever)\b",
        "browser_fill": r"\b(?:preencha|preencher|complete|completar)\b",
        "desktop_focus_window": r"\b(?:foque|foca|focar|traga|trazer|coloque|colocar)\b",
        "desktop_open_application": r"\b(?:abra|abre|abrir|inicie|inicia|iniciar)\b",
        "desktop_move_pointer_to_window": r"\b(?:mova|move|mover|leve|levar)\b.*\b(?:mouse|ponteiro|cursor)\b",
        "desktop_press_escape": r"\b(?:pressione|pressionar|aperte|apertar)\b.*\b(?:escape|esc)\b",
        "desktop_close_window": r"\b(?:feche|fecha|fechar|encerre|encerrar)\b",
    }

    def validate(
        self,
        user_message: str,
        tool: Tool,
        arguments: dict[str, Any],
    ) -> ToolIntentDecision:
        normalized_message = self._normalize(user_message)
        effect_pattern = self._EFFECT_INTENT_PATTERNS.get(tool.name)
        if (
            effect_pattern is not None
            and re.search(effect_pattern, normalized_message) is None
        ):
            return ToolIntentDecision(
                allowed=False,
                response=(
                    "Essa ação não está explícita no pedido do usuário. "
                    "Informe diretamente a ação que deseja executar."
                ),
            )

        if tool.risk < RiskLevel.SENSITIVE:
            return ToolIntentDecision(allowed=True)

        targets = [
            arguments.get(name)
            for name in self._TARGET_ARGUMENTS
            if isinstance(arguments.get(name), str)
        ]
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
