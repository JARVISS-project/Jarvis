"""Grounding determinístico para impedir alegações de execução sem Tool."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from collections.abc import Collection


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
        r"\b(?:pesquisei|abri|criei|editei|escrevi|anotei|adicionei|acrescentei|"
        r"apaguei|exclui|deletei|movi|renomeei|li|listei|procurei|busquei|"
        r"encontrei|localizei|verifiquei|confirmei|executei|rodei|testei|"
        r"instalei|desinstalei|baixei|salvei|iniciei|encerrei|organizei|"
        r"reorganizei|formatei|estruturei|corrigi|atualizei|foquei|"
        r"focalizei|trouxe|pressionei|observei|capturei|fechei|"
        r"marquei|agendei|remarquei|alterei|modifiquei|cancelei|"
        r"exclui|deletei|removi)\b"
    )
    _RESULT_CLAIM = re.compile(
        r"\b(?:essas|estes|esses|aqui)\s+(?:sao|estao)\s+(?:as|os)\s+"
        r"(?:notas|arquivos|resultados)\b"
        r"|\bagora\s+(?:esta|ficou)\s+(?:mais\s+)?"
        r"(?:limpa|limpo|organizada|organizado|estruturada|estruturado)\b"
        r"|\b(?:o|a)\s+(?:aplicativo|programa|janela)\s+(?:esta|ficou)\s+"
        r"(?:aberto|aberta|em\s+foco|em\s+primeiro\s+plano|em\s+execucao)\b"
        r"|\bo\s+ponteiro\s+(?:esta|ficou)\s+(?:agora\s+)?"
        r"(?:posicionado\s+)?(?:no\s+centro|na\s+janela)\b"
        r"|\bescape\s+(?:foi\s+enviado|foi\s+pressionad[oa]|pressionad[oa])\b"
        r"|\ba\s+janela(?:\s+[a-z0-9]+){0,8}\s+esta\s+aberta\b"
        r"|\bo\s+botao(?:\s+de)?\s+escape\s+esta\s+visivel\b"
        r"|\ba\s+tela\s+(?:mudou|nao\s+mudou|foi\s+alterada)\b"
        r"|\b(?:a\s+|sua\s+)?agenda\s+(?:esta|ficou)\s+(?:livre|vazia)\b"
        r"|\b(?:nao\s+ha|nao\s+existem|nenhum|nenhuma)\s+"
        r"(?:evento|eventos|compromisso|compromissos)\b"
        r"|\b(?:voce|o\s+periodo|esse\s+periodo|esse\s+horario)\s+"
        r"(?:esta|ficou)\s+(?:livre|ocupado|ocupada)\b"
        r"|\b(?:o\s+evento|o\s+compromisso)\s+(?:foi\s+)?(?:criado|agendado|marcado|"
        r"atualizado|alterado|modificado|cancelado|excluido|removido)\b"
        r"|\b(?:adicionei|coloquei|pus)\s+(?:o\s+evento|a\s+reuniao|o\s+compromisso)\b"
        r"|\b(?:mudei|alterei|modifiquei)\s+(?:o\s+horario|a\s+data|o\s+evento)\b"
    )
    _CALENDAR_CREATE_CLAIM = re.compile(
        r"\b(?:marquei|agendei)\b"
        r"|\b(?:criei|adicionei|coloquei|pus)\b(?:\s+[a-z0-9]+){0,6}\s+"
        r"(?:evento|compromisso|reuniao|agenda)\b"
        r"|\b(?:evento|compromisso|reuniao)\b(?:\s+[a-z0-9]+){0,4}\s+"
        r"(?:foi\s+)?"
        r"(?:criad[oa]|agendad[oa]|marcad[oa])\b"
    )
    _CALENDAR_UPDATE_CLAIM = re.compile(
        r"\bremarquei\b"
        r"|\b(?:mudei|alterei|modifiquei|atualizei)\b"
        r"(?:\s+[a-z0-9]+){0,6}\s+"
        r"(?:evento|compromisso|reuniao|horario|data|agenda)\b"
        r"|\b(?:evento|compromisso|reuniao|horario|data)\b"
        r"(?:\s+[a-z0-9]+){0,4}\s+"
        r"(?:foi\s+)?(?:atualizad[oa]|alterad[oa]|modificad[oa]|remarcad[oa])\b"
    )
    _UNSUPPORTED_CALENDAR_WRITE_CLAIM = re.compile(
        r"\bcancelei\b"
        r"|\b(?:exclui|deletei|removi)\b(?:\s+[a-z0-9]+){0,6}\s+"
        r"(?:evento|compromisso|reuniao|agenda)\b"
        r"|\b(?:evento|compromisso|reuniao)\b(?:\s+[a-z0-9]+){0,4}\s+"
        r"(?:foi\s+)?(?:cancelad[oa]|excluid[oa]|removid[oa])\b"
    )

    def validate(
        self,
        text: str | None,
        *,
        tool_executed: bool,
        successful_tool_names: Collection[str] = (),
    ) -> GroundingDecision:
        normalized = self.normalize(text or "")
        successful = set(successful_tool_names)
        if re.search(r"\bpesquisei\b", normalized) and not successful.intersection({
                'safe_web_search', 'browser_extract_text', 'browser_extract_multiple',
                'obsidian_search_notes', 'list_files'}):
            return GroundingDecision(False, "research_claim_without_verified_tool")
        if self._UNSUPPORTED_CALENDAR_WRITE_CLAIM.search(normalized):
            return GroundingDecision(False, "unsupported_calendar_write_claim")
        if self._CALENDAR_CREATE_CLAIM.search(normalized) and (
            "google_calendar_create_event" not in successful
        ):
            return GroundingDecision(False, "calendar_create_claim_without_verified_tool")
        if self._CALENDAR_UPDATE_CLAIM.search(normalized) and (
            "google_calendar_update_event" not in successful
        ):
            return GroundingDecision(False, "calendar_update_claim_without_verified_tool")
        if tool_executed:
            return GroundingDecision(True)
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
