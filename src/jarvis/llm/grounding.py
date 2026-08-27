"""Grounding determinístico para impedir alegações de execução sem Tool."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class GroundingDecision:
    accepted: bool
    reason: str = "accepted"


class ExecutionClaimGuard:
    """Bloqueia texto que descreve ação real quando nenhuma Tool executou.

    O guard não tenta decidir se a resposta está semanticamente correta. Ele
    cobre apenas a regra operacional do Jarvis: ações e observações do sistema
    precisam ter um resultado real de Tool na interação atual.
    """

    _COMPLETED_ACTION = re.compile(
        r"\b(?:abri|criei|editei|escrevi|anotei|adicionei|acrescentei|"
        r"apaguei|exclui|deletei|movi|renomeei|li|listei|procurei|busquei|"
        r"encontrei|localizei|verifiquei|confirmei|executei|rodei|testei|"
        r"instalei|desinstalei|baixei|salvei|iniciei|encerrei|organizei|"
        r"reorganizei|formatei|estruturei|corrigi|atualizei)\b"
    )
    _RESULT_CLAIM = re.compile(
        r"\b(?:essas|estes|esses|aqui)\s+(?:sao|estao)\s+(?:as|os)\s+"
        r"(?:notas|arquivos|resultados)\b"
        r"|\bagora\s+(?:esta|ficou)\s+(?:mais\s+)?"
        r"(?:limpa|limpo|organizada|organizado|estruturada|estruturado)\b"
    )

    def validate(
        self,
        text: str | None,
        *,
        tool_executed: bool,
    ) -> GroundingDecision:
        if tool_executed:
            return GroundingDecision(True)

        normalized = self.normalize(text or "")
        if self._COMPLETED_ACTION.search(normalized):
            return GroundingDecision(False, "execution_claim_without_tool")
        if self._RESULT_CLAIM.search(normalized):
            return GroundingDecision(False, "result_claim_without_tool")
        return GroundingDecision(True)

    @staticmethod
    def normalize(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
