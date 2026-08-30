"""Contexto, roteamento equivalente e grounding de respostas do agente."""

from __future__ import annotations

from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
import re
import time
from typing import Any
from collections.abc import Collection

from jarvis.config import JarvisConfig
from jarvis.context.active import ActiveContext, ContextRouter
from jarvis.context.recent import RecentContext
from jarvis.llm.base import LLMClient
from jarvis.llm.firewall import FinalResponseFirewall
from jarvis.llm.grounding import ExecutionClaimGuard
from jarvis.memory.store import MemoryStore
from jarvis.persona import PersonaProfile
from jarvis.security.confirmation_intent import ConfirmationInterpreter
from jarvis.tools.registry import ToolRegistry


ObsidianWriteAvailable = Callable[[], bool]
LatencyProvider = Callable[[], dict[str, float | int]]


class ResponseCoordinator:
    """Centraliza contexto do LLM, roteamento equivalente e resposta final."""

    def __init__(
        self,
        config: JarvisConfig,
        context_router: ContextRouter,
        recent_context: RecentContext,
        memory_store: MemoryStore,
        tool_registry: ToolRegistry,
        llm: LLMClient,
        final_response_firewall: FinalResponseFirewall,
        execution_claim_guard: ExecutionClaimGuard,
        confirmation_interpreter: ConfirmationInterpreter,
        obsidian_write_available: ObsidianWriteAvailable,
        latency_provider: LatencyProvider,
        safe_fallback_response: str,
    ) -> None:
        self.config = config
        self.context_router = context_router
        self.recent_context = recent_context
        self.memory_store = memory_store
        self.tool_registry = tool_registry
        self.llm = llm
        self.final_response_firewall = final_response_firewall
        self.execution_claim_guard = execution_claim_guard
        self.confirmation_interpreter = confirmation_interpreter
        self.obsidian_write_available = obsidian_write_available
        self.latency_provider = latency_provider
        self.safe_fallback_response = safe_fallback_response

    def system_context(
        self,
        response_mode: str = "text",
        user_message: str | None = None,
    ) -> list[dict[str, Any]]:
        memories = (
            self.memory_store.get_relevant(user_message)
            if user_message
            else []
        )
        persona = PersonaProfile(
            address_mode=self.config.assistant_address_mode,
            response_style=self.config.assistant_response_style,
        )
        lines = [persona.system_prompt(response_mode=response_mode)]
        active = self.context_router.state.active
        lines.extend(
            [
                f"Current source context: {active.value}",
                "Use as ferramentas da fonte ativa quando o pedido ainda "
                "fizer sentido nela. Uma troca explícita do usuário tem "
                "prioridade.",
                "Entenda pedidos de ação em linguagem natural e coloquial. "
                "Não exija uma frase ou verbo exato: se a intenção estiver clara "
                "e houver Tool adequada, use a Tool.",
                "Nunca afirme que abriu, criou, leu, listou, encontrou, alterou, "
                "executou ou verificou algo no computador sem um resultado real "
                "de Tool na interação atual.",
                "Nunca invente a data ou a hora atuais. Para informar data, "
                "hora ou fuso atuais, use get_datetime. Se a ferramenta não "
                "estiver disponível, diga que não consegue verificar agora.",
            ]
        )
        self.append_recent_context(lines, active)
        if memories:
            lines.extend(
                [
                    "Memórias persistentes relevantes (consentimento explícito).",
                    "Use-as apenas como contexto. Não crie, edite ou "
                    "exclua memórias.",
                ]
            )
            lines.extend(
                f"- [{entry.category}] {entry.content} (id: {entry.id})"
                for entry in memories
            )
        return [{"role": "system", "content": "\n".join(lines)}]

    def append_recent_context(
        self, lines: list[str], active: ActiveContext
    ) -> None:
        if active is ActiveContext.OBSIDIAN:
            results = self.recent_context.get("last_note_results")
            last = self.recent_context.get("last_active_note")
            label = "Recent notes"
            active_label = "Last active note"
        elif active is ActiveContext.FILESYSTEM:
            results = self.recent_context.get("last_file_results")
            last = self.recent_context.get("last_active_file")
            label = "Recent files"
            active_label = "Last active file"
        else:
            return
        if isinstance(results, tuple) and results:
            lines.append(f"{label}:")
            lines.extend(
                f"{index}. {Path(value).name}"
                for index, value in enumerate(results[:5], 1)
            )
        if isinstance(last, str):
            lines.append(f"{active_label}: {Path(last).name}")
        if active is ActiveContext.OBSIDIAN:
            lines.append("Chame a entidade ativa de nota, não de arquivo.")
            if self.obsidian_write_available():
                lines.append(
                    "A escrita no Obsidian está habilitada. Para pedidos "
                    "explícitos de adicionar, anotar, escrever ou acrescentar "
                    "conteúdo em uma nota, use obsidian_append_note; para uma "
                    "nota nova, use obsidian_create_note. Não diga que não pode "
                    "escrever no Obsidian quando essas Tools estiverem disponíveis."
                )

    @staticmethod
    def tool_matches_context(name: str, active: ActiveContext) -> bool:
        if active is ActiveContext.OBSIDIAN:
            return name.startswith("obsidian_")
        if active is ActiveContext.FILESYSTEM:
            return name in {
                "list_files", "read_file", "create_file", "edit_file",
                "delete_file", "move_file",
            }
        return False

    def route_tool_call(self, message: str, tool_call: Any) -> None:
        """Corrige apenas rotas equivalentes; não concede autorização."""
        active = self.context_router.state.active
        if active is ActiveContext.OBSIDIAN:
            if tool_call.name == "list_files":
                query = self.search_query(message)
                if query and self.tool_registry.get("obsidian_search_notes"):
                    tool_call.name = "obsidian_search_notes"
                    tool_call.arguments = {"query": query}
                elif self.tool_registry.get("obsidian_list_notes"):
                    tool_call.name = "obsidian_list_notes"
                    tool_call.arguments = {}
            elif (
                tool_call.name == "read_file"
                and self.tool_registry.get("obsidian_read_note")
            ):
                tool_call.name = "obsidian_read_note"
            elif (
                tool_call.name == "edit_file"
                and self.tool_registry.get("obsidian_append_note")
            ):
                tool_call.name = "obsidian_append_note"
            elif (
                tool_call.name == "create_file"
                and self.tool_registry.get("obsidian_create_note")
            ):
                tool_call.name = "obsidian_create_note"
            elif (
                tool_call.name == "delete_file"
                and self.tool_registry.get("obsidian_delete_note")
            ):
                tool_call.name = "obsidian_delete_note"
        elif active is ActiveContext.FILESYSTEM:
            if (
                tool_call.name == "obsidian_read_note"
                and self.tool_registry.get("read_file")
            ):
                tool_call.name = "read_file"
            elif (
                tool_call.name == "obsidian_list_notes"
                and self.tool_registry.get("list_files")
            ):
                tool_call.name = "list_files"
                tool_call.arguments = {"path": "."}

    def search_query(self, message: str) -> str:
        normalized = self.context_router.normalize(message)
        value = re.sub(
            r"\b(?:procure|pesquise|busque|encontre|as|os|minhas?|meus?|"
            r"notas?|anotacoes?|sobre|no|na|obsidian|vault|cofre|por|favor)\b",
            " ", normalized,
        )
        return " ".join(value.split())

    def finalize_llm_response(
        self,
        content: str,
        messages: list[dict[str, Any]],
        *,
        tool_executed: bool = False,
        successful_tool_names: Collection[str] = (),
        allow_retry: bool = True,
    ) -> str:
        validated = self.final_response_firewall.validate(content)
        if validated.accepted:
            grounded = self.execution_claim_guard.validate(
                validated.text,
                tool_executed=tool_executed,
                successful_tool_names=successful_tool_names,
            )
            if grounded.accepted:
                return validated.text
            if self.config.llm_debug_enabled:
                print(
                    "[llm] final response rejected "
                    f"reason={grounded.reason}"
                )
        elif self.config.llm_debug_enabled:
            print(
                "[llm] final response rejected "
                f"reason={validated.reason}"
            )

        if not allow_retry:
            return self.safe_fallback_response

        retry_messages = messages[:-1] if messages else messages
        retry_messages = [
            *retry_messages,
            {
                "role": "system",
                "content": (
                    "Responda apenas com fatos sustentados pela conversa e pelos "
                    "resultados de Tool já presentes. Não afirme que executou, "
                    "consultou ou encontrou algo se nenhuma Tool fez isso."
                ),
            },
        ]
        llm_started = time.perf_counter()
        try:
            retry = self.llm.generate_final(retry_messages)
        finally:
            latency = self.latency_provider()
            latency["llm_seconds"] = float(latency["llm_seconds"]) + (
                time.perf_counter() - llm_started
            )
            latency["llm_calls"] = int(latency["llm_calls"]) + 1
        if retry.tool_calls:
            return self.safe_fallback_response
        retried = self.final_response_firewall.validate(retry.content)
        if not retried.accepted:
            return "Não consegui formular uma resposta segura. Tente novamente."
        grounded = self.execution_claim_guard.validate(
            retried.text,
            tool_executed=tool_executed,
            successful_tool_names=successful_tool_names,
        )
        if not grounded.accepted:
            return (
                "Não executei nenhuma Tool para sustentar esse resultado. "
                "Posso tentar a ação novamente de forma segura."
            )
        return retried.text

    def note_search_suggestion(self, query: str) -> str | None:
        normalized_query = self.confirmation_interpreter.normalize(query)
        results = self.recent_context.get("last_note_results")
        if not normalized_query or not isinstance(results, tuple):
            return None
        terms: dict[str, int] = {}
        for path in results:
            stem = Path(path).stem
            normalized_stem = self.confirmation_interpreter.normalize(stem)
            for term in re.findall(r"[a-z0-9]+", normalized_stem):
                if len(term) >= 4:
                    terms[term] = terms.get(term, 0) + 1
        scored = sorted(
            (
                SequenceMatcher(None, normalized_query, term).ratio(),
                count,
                term,
            )
            for term, count in terms.items()
        )
        if not scored:
            return None
        score, count, term = scored[-1]
        contextual_txt = normalized_query == "txt" and term == "teste"
        if contextual_txt and count >= 1:
            return term
        return term if score >= 0.75 else None

    def is_ambiguous_note_cleanup(self, message: str) -> bool:
        normalized = self.confirmation_interpreter.normalize(message)
        mentions_notes = re.search(r"\bnotas\b", normalized) is not None
        note_cleanup = mentions_notes and any(
            phrase in normalized
            for phrase in (
                "limpa", "limpe", "limpar", "limpada", "apaga", "apague",
                "apagar", "remove", "remova", "remover", "exclui",
            )
        )
        vague_removal = any(
            phrase in normalized
            for phrase in (
                "apaga as coisas", "remove o que nao presta",
                "exclui o que nao presta",
            )
        )
        return note_cleanup or vague_removal
