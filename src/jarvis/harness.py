from typing import Any
from uuid import uuid4
from difflib import SequenceMatcher
from pathlib import Path
import re

from jarvis.security.authorization import AuthorizationGrant

from jarvis.config import JarvisConfig
from jarvis.context.recent import RecentContext
from jarvis.context.active import ActiveContext, ContextRouter
from jarvis.context.pending_action import (
    PendingAction,
    PendingActionContext,
    PendingActionState,
)
from jarvis.context.note_reference_resolver import (
    NoteMatchKind,
    NoteReferenceResolver,
)
from jarvis.context.file_reference_resolver import (
    FileMatchKind,
    FileReferenceResolver,
)
from jarvis.doctor import JarvisDoctor
from jarvis.llm.base import LLMClient
from jarvis.llm.firewall import FinalResponseFirewall
from jarvis.llm.tool_schema import tool_to_ollama_schema
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.memory.store import MemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.cancellation import CancellationInterpreter
from jarvis.security.risk import RiskLevel
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.confirmation_request import (
    ConfirmationPurpose,
    ConfirmationRequest,
)
from jarvis.security.confirmation_intent import (
    ConfirmationIntent,
    ConfirmationInterpreter,
)
from jarvis.security.tool_intent import ToolIntentGuard
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.validator import ToolArgumentValidator


class Harness:
    SAFE_FALLBACK_RESPONSE = (
        "Não consegui concluir essa resposta. Tente novamente."
    )
    BASE_SYSTEM_PROMPT = (
        "Você é Jarvis, um assistente pessoal local. Responda em português "
        "quando o usuário falar português e responda diretamente. Não "
        "exponha raciocínio interno nem use tags <think> ou <response> na "
        "resposta final."
    )

    def __init__(
        self,
        llm: LLMClient,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        memory: ConversationMemory,
        memory_store: MemoryStore | None = None,
        config: JarvisConfig | None = None,
        resource_manager: ResourceManager | None = None,
        recent_context: RecentContext | None = None,
        file_reference_resolver: FileReferenceResolver | None = None,
        target_confirmation: ConfirmationProvider | None = None,
        pending_actions: PendingActionContext | None = None,
        note_reference_resolver: NoteReferenceResolver | None = None,
        context_router: ContextRouter | None = None,
    ) -> None:
        self.llm = llm
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.memory = memory
        self.memory_store = (
            memory_store
            if memory_store is not None
            else InMemoryMemoryStore()
        )
        self.config = config or JarvisConfig()
        self.resource_manager = (
            resource_manager
            if resource_manager is not None
            else ResourceManager()
        )
        self.argument_validator = ToolArgumentValidator()
        self.intent_guard = ToolIntentGuard()
        self.recent_context = recent_context or RecentContext()
        self.file_reference_resolver = file_reference_resolver
        self.target_confirmation = target_confirmation
        self.pending_actions = pending_actions or PendingActionContext(
            self.config.pending_action_timeout_seconds
        )
        self.cancellation_interpreter = CancellationInterpreter()
        self.confirmation_interpreter = ConfirmationInterpreter()
        self.note_reference_resolver = note_reference_resolver
        self.context_router = context_router or ContextRouter()
        self.final_response_firewall = FinalResponseFirewall()
        self.doctor = JarvisDoctor(
            self.config,
            self.resource_manager,
            self.memory_store,
        )

    def run(self) -> None:
        print("Jarvis iniciado.")
        print("Digite 'sair' para encerrar.\n")

        while True:
            mensagem = input("Você: ")

            if mensagem.lower() == "sair":
                print("Jarvis: Até mais.")
                break

            resposta = self.processar_mensagem(mensagem)
            print(f"Jarvis: {resposta}")

    def processar_mensagem(
        self,
        mensagem: str,
        manage_runtime: bool = True,
    ) -> str:
        stripped_message = mensagem.strip()
        command = stripped_message.lower()

        if command == "/doctor":
            return self.doctor.report()

        if command == "/memory":
            count = self.memory.count()
            label = "mensagem" if count == 1 else "mensagens"
            return f"Memória da conversa: {count} {label}."

        if command == "/clear":
            self.memory.clear()
            return "Memória da conversa limpa."

        if command == "/remember" or command.startswith("/remember "):
            content = stripped_message[len("/remember"):].strip()
            if not content:
                return "Uso: /remember <texto>"

            entry = self.memory_store.add(content, category="user")
            return f"Memória armazenada com ID: {entry.id}."

        if command == "/memories":
            memories = self.memory_store.get_all()
            if not memories:
                return "Nenhuma memória persistente armazenada."

            lines = ["Memórias persistentes:"]
            lines.extend(
                f"- {entry.id} | {entry.category} | {entry.content}"
                for entry in memories
            )
            return "\n".join(lines)

        if command == "/forget" or command.startswith("/forget "):
            memory_id = stripped_message[len("/forget"):].strip()
            if not memory_id:
                return "Uso: /forget <id>"

            if self.memory_store.delete(memory_id):
                return "Memória removida."

            return "Memória não encontrada."

        if command == "/gamemode on":
            self.resource_manager.enable_game_mode()
            return "Game Mode ativado."

        if command == "/gamemode off":
            self.resource_manager.disable_game_mode()
            return "Game Mode desativado."

        if command == "/gamemode" or command.startswith("/gamemode "):
            return "Uso: /gamemode on|off"

        if command == "/status":
            game_mode = (
                "ativado"
                if self.resource_manager.is_game_mode_active()
                else "desativado"
            )
            return (
                f"Estado: {self.resource_manager.state.name} | "
                f"Game Mode: {game_mode} | "
                f"Modelo: {self.config.model_name} | "
                f"Conversa: {self.memory.count()} | "
                f"Memórias: {len(self.memory_store.get_all())}"
            )

        if not manage_runtime:
            return self._process_active_message(mensagem)

        self.resource_manager.activate()
        try:
            return self._process_active_message(mensagem)
        finally:
            self.resource_manager.idle()

    def has_pending_action(self) -> bool:
        return self.pending_actions.has_pending()

    def _process_active_message(self, mensagem: str) -> str:
        self.context_router.update(mensagem)
        if self._is_ambiguous_note_cleanup(mensagem):
            self._cancel_pending_action()
            return (
                "Posso listar as notas que parecem ser de teste e você "
                "escolhe quais deseja revisar ou remover. Qual escopo devo usar?"
            )
        runtime_response = self._runtime_info_response(mensagem)
        if runtime_response is not None:
            return runtime_response
        pending_response = self._process_pending_message(mensagem)
        if pending_response is not None:
            return pending_response
        keep_alive = (
            self.config.ollama_keep_alive_game_mode
            if self.resource_manager.is_game_mode_active()
            else self.config.ollama_keep_alive_normal
        )
        self.llm.set_keep_alive(keep_alive)
        return self._process_normal_message(mensagem)

    def _process_normal_message(self, mensagem: str) -> str:

        available_tools = self.tool_registry.list_tools()
        active_context = self.context_router.state.active
        available_tools.sort(
            key=lambda tool: not self._tool_matches_context(
                tool.name, active_context
            )
        )
        tools = [tool_to_ollama_schema(tool) for tool in available_tools]

        self.memory.add_user_message(mensagem)
        messages: list[dict[str, Any]] = self._system_context()
        messages.extend(self.memory.get_history())

        for _ in range(self.config.max_tool_iterations):
            response = self.llm.generate(
                messages=messages,
                tools=tools,
            )
            for tool_call in response.tool_calls:
                self._route_tool_call(mensagem, tool_call)
            if response.tool_calls:
                # Conteúdo textual adjacente a uma tool call é protocolo
                # interno e nunca é candidato a resposta final.
                response.content = ""
            messages.append(response.to_message())

            if not response.tool_calls:
                final_response = self._finalize_llm_response(
                    response.content, messages
                )
                self.memory.add_assistant_message(final_response)
                return final_response

            for tool_call in response.tool_calls:
                tool = self.tool_registry.get(tool_call.name)

                if tool is None:
                    tool_result = (
                        f"Tool não permitida ou inexistente: "
                        f"'{tool_call.name}'."
                    )

                else:
                    valid, error = self.argument_validator.validate(
                        tool,
                        tool_call.arguments,
                    )

                    if not valid:
                        tool_result = (
                            f"Chamada de Tool rejeitada: {error}"
                        )

                    else:
                        intent = self.intent_guard.validate(
                            mensagem,
                            tool,
                            tool_call.arguments,
                        )
                        if not intent.allowed:
                            clarification = intent.response or (
                                "Informe um alvo explícito para essa ação."
                            )
                            missing_field = self._missing_target_field(
                                tool_call.arguments
                            )
                            if missing_field is not None:
                                remaining = dict(tool_call.arguments)
                                state = PendingActionState.WAITING_TARGET
                                if intent.requires_intent_confirmation:
                                    state = (
                                        PendingActionState
                                        .WAITING_INTENT_CONFIRMATION
                                    )
                                else:
                                    remaining.pop(missing_field, None)
                                self.pending_actions.set(
                                    PendingAction(
                                        tool_name=tool.name,
                                        original_request=mensagem,
                                        missing_field=missing_field,
                                        resolved_arguments=remaining,
                                        state=state,
                                    )
                                )
                            self.memory.add_assistant_message(clarification)
                            return clarification

                        (
                            resolved_arguments,
                            resolution_response,
                            prepared_grant,
                            interaction_id,
                        ) = (
                            self._resolve_note_reference(
                                tool,
                                tool_call.arguments,
                            )
                        )
                        if (
                            resolution_response is None
                            and prepared_grant is None
                        ):
                            (
                                resolved_arguments,
                                resolution_response,
                                prepared_grant,
                                interaction_id,
                            ) = self._resolve_file_reference(
                                tool, resolved_arguments
                            )
                        if resolution_response is not None:
                            self.memory.add_assistant_message(
                                resolution_response
                            )
                            return resolution_response

                        execution_result = self._execute_tool(
                            tool, resolved_arguments,
                            prepared_grant, interaction_id,
                        )
                        tool_result = execution_result.content
                        if (
                            tool.name == "obsidian_search_notes"
                            and execution_result.status == "success"
                            and tool_result == "Nenhuma nota encontrada."
                        ):
                            suggestion = self._note_search_suggestion(
                                str(resolved_arguments.get("query", ""))
                            )
                            if suggestion is not None:
                                clarification = (
                                    f"Você quis dizer '{suggestion}'?"
                                )
                                self.pending_actions.set(
                                    PendingAction(
                                        tool_name=tool.name,
                                        original_request=mensagem,
                                        missing_field="query",
                                        resolved_arguments={
                                            "query": suggestion
                                        },
                                        state=(
                                            PendingActionState
                                            .WAITING_TARGET_CONFIRMATION
                                        ),
                                    )
                                )
                                self.memory.add_assistant_message(
                                    clarification
                                )
                                return clarification
                        if execution_result.status == "success":
                            self._remember_operational_context(
                                tool.name,
                                resolved_arguments,
                                execution_result.content,
                            )
                        if (
                            tool.risk >= RiskLevel.MEDIUM
                            and execution_result.status == "not_found"
                        ):
                            not_found = "Arquivo não encontrado."
                            self.memory.add_assistant_message(not_found)
                            return not_found
                        if (
                            tool.risk >= RiskLevel.HIGH
                            and execution_result.status
                            in {"denied", "security_blocked"}
                        ):
                            cancellation = (
                                "A exclusão foi cancelada."
                                if tool.name == "delete_file"
                                else "A ação foi cancelada."
                            )
                            self.memory.add_assistant_message(cancellation)
                            return cancellation

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_call.name,
                        "content": str(tool_result),
                    }
                )

        final_response = "Limite de chamadas de ferramentas atingido."
        self.memory.add_assistant_message(final_response)
        return final_response

    def _execute_tool(
        self,
        tool: Any,
        arguments: dict[str, Any],
        grant: AuthorizationGrant | None = None,
        interaction_id: str | None = None,
    ) -> ToolExecutionResult:
        execute_with_grant = getattr(
            self.tool_executor, "execute_with_grant", None
        )
        if (
            grant is not None
            and interaction_id is not None
            and callable(execute_with_grant)
        ):
            return execute_with_grant(
                tool, grant, interaction_id, **arguments
            )
        execute_with_result = getattr(
            self.tool_executor,
            "execute_with_result",
            None,
        )
        if callable(execute_with_result):
            return execute_with_result(tool, **arguments)

        content = self.tool_executor.execute(tool, **arguments)
        return ToolExecutionResult(str(content), "success", True)

    def _resolve_file_reference(
        self,
        tool: Any,
        arguments: dict[str, Any],
    ) -> tuple[
        dict[str, Any],
        str | None,
        AuthorizationGrant | None,
        str | None,
    ]:
        resolver = self.file_reference_resolver
        path = arguments.get("path")
        if (
            resolver is None
            or tool.name not in {"read_file", "edit_file", "delete_file"}
            or not isinstance(path, str)
        ):
            return arguments, None, None, None
        match = resolver.resolve(path)
        if match.kind is FileMatchKind.AMBIGUOUS:
            options = ", ".join(match.candidates)
            return (
                arguments,
                f"Qual arquivo você quis dizer? {options}",
                None,
                None,
            )
        if match.kind is FileMatchKind.NOT_FOUND or match.path is None:
            return arguments, None, None, None
        if match.kind is FileMatchKind.EXACT_LITERAL:
            return arguments, None, None, None

        requires_identity_confirmation = (
            tool.risk >= RiskLevel.MEDIUM
            or match.kind in {FileMatchKind.FUZZY, FileMatchKind.CONTEXT}
        )
        if requires_identity_confirmation:
            confirmation = self.target_confirmation
            request = ConfirmationRequest(
                action="confirm_file_target",
                target=match.path,
                risk=RiskLevel.LOW,
                prompt_text=f"Você quis dizer {match.path}?",
                purpose=ConfirmationPurpose.TARGET,
            )
            result = (
                confirmation.confirm_result(request)
                if confirmation is not None
                else None
            )
            if result is None or not result.target_confirmed:
                self._cancel_pending_action()
                return arguments, "Cancelado.", None, None
            if result.action_authorized:
                resolved = dict(arguments)
                resolved["path"] = match.path
                interaction_id = str(uuid4())
                grant = self._grant_compound_authorization(
                    tool, resolved, interaction_id
                )
                return resolved, None, grant, interaction_id
        resolved = dict(arguments)
        resolved["path"] = match.path
        return resolved, None, None, None

    def _remember_operational_context(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_content: str = "",
    ) -> None:
        self.recent_context.set("last_tool_name", tool_name)
        if tool_name.startswith("obsidian_"):
            self.context_router.state.active = ActiveContext.OBSIDIAN
        elif tool_name in {
            "list_files", "read_file", "create_file", "edit_file",
            "delete_file", "move_file",
        }:
            self.context_router.state.active = ActiveContext.FILESYSTEM
        if tool_name in {
            "obsidian_list_notes", "obsidian_search_notes"
        }:
            query = arguments.get("query")
            if isinstance(query, str) and query.strip():
                self.recent_context.set("last_search_query", query.strip())
            note_paths: list[str] = []
            for line in result_content.splitlines():
                candidate = line
                if line.startswith("- "):
                    candidate = line[2:].split(" | ", 1)[0]
                if candidate.casefold().endswith(".md"):
                    note_paths.append(candidate.strip())
            self.recent_context.set("last_note_results", note_paths)
            return
        path = arguments.get("path")
        if tool_name == "list_files" and path is None:
            path = "."
        if not isinstance(path, str):
            return
        if tool_name.startswith("obsidian_"):
            self.recent_context.set("last_note", path)
            self.recent_context.set("last_active_note", path)
        elif tool_name == "list_files":
            self.recent_context.set("last_directory", path)
            directory = "" if path in {"", "."} else path.strip("/\\")
            listed_files = []
            for line in result_content.splitlines():
                if not line.startswith("[FILE] "):
                    continue
                name = line[len("[FILE] "):].strip()
                listed_files.append(
                    f"{directory}/{name}" if directory else name
                )
            self.recent_context.set(
                "last_search_results", tuple(listed_files)
            )
            self.recent_context.set("last_file_results", listed_files)
        else:
            self.recent_context.set("last_file", path)
            self.recent_context.set("last_active_file", path)

    def _resolve_note_reference(
        self,
        tool: Any,
        arguments: dict[str, Any],
    ) -> tuple[
        dict[str, Any],
        str | None,
        AuthorizationGrant | None,
        str | None,
    ]:
        resolver = self.note_reference_resolver
        path = arguments.get("path")
        if (
            resolver is None
            or tool.name not in {
                "obsidian_read_note",
                "obsidian_open_note",
                "obsidian_append_note",
                "obsidian_delete_note",
            }
            or not isinstance(path, str)
        ):
            return arguments, None, None, None
        match = resolver.resolve(path)
        if match.kind is NoteMatchKind.AMBIGUOUS:
            options = ", ".join(match.candidates)
            return (
                arguments,
                f"Qual nota você quis dizer? {options}",
                None,
                None,
            )
        if match.kind is NoteMatchKind.NOT_FOUND or match.canonical_path is None:
            return arguments, None, None, None
        if match.kind is NoteMatchKind.EXACT_LITERAL:
            return arguments, None, None, None

        resolved = dict(arguments)
        resolved["path"] = match.canonical_path
        if tool.risk < RiskLevel.MEDIUM:
            return resolved, None, None, None
        request = ConfirmationRequest(
            action="confirm_note_target",
            target=match.canonical_path,
            risk=RiskLevel.LOW,
            prompt_text=f"Você quis dizer a nota {match.canonical_path}?",
            purpose=ConfirmationPurpose.TARGET,
        )
        confirmation = self.target_confirmation
        result = (
            confirmation.confirm_result(request)
            if confirmation is not None
            else None
        )
        if result is None or not result.target_confirmed:
            self._cancel_pending_action()
            return arguments, "Cancelado.", None, None
        if result.action_authorized:
            interaction_id = str(uuid4())
            grant = self._grant_compound_authorization(
                tool, resolved, interaction_id
            )
            return resolved, None, grant, interaction_id
        return resolved, None, None, None

    @staticmethod
    def _missing_target_field(arguments: dict[str, Any]) -> str | None:
        for field in ("path", "target", "source"):
            if field in arguments:
                return field
        return None

    def _process_pending_message(self, message: str) -> str | None:
        pending = self.pending_actions.get()
        if pending is None:
            return None
        if self.cancellation_interpreter.is_cancellation(message):
            self._cancel_pending_action()
            return "Cancelado."
        if (
            pending.missing_field == "query"
            and pending.state
            is PendingActionState.WAITING_TARGET_CONFIRMATION
        ):
            intent = self.confirmation_interpreter.interpret(message)
            if intent is not ConfirmationIntent.CONFIRM:
                self._cancel_pending_action()
                return "Cancelado."
            tool = self.tool_registry.get(pending.tool_name)
            if tool is None:
                self._cancel_pending_action()
                return "Cancelado."
            arguments = dict(pending.resolved_arguments)
            self.pending_actions.clear()
            result = self._execute_tool(tool, arguments)
            if result.status == "success":
                self._remember_operational_context(
                    tool.name, arguments, result.content
                )
            return result.content
        if pending.state is PendingActionState.WAITING_INTENT_CONFIRMATION:
            intent = self.confirmation_interpreter.interpret(message)
            if intent is not ConfirmationIntent.CONFIRM:
                self._cancel_pending_action()
                return "Cancelado."
            spoken_target = pending.resolved_arguments.get(
                pending.missing_field
            )
            if not isinstance(spoken_target, str):
                self._cancel_pending_action()
                return "Cancelado."
            return self._resolve_and_execute_pending(pending, spoken_target)
        if pending.missing_field != "path":
            self._cancel_pending_action()
            return "Ação cancelada por falta de um alvo válido."
        resolver = self.file_reference_resolver
        tool = self.tool_registry.get(pending.tool_name)
        if resolver is None or tool is None:
            self._cancel_pending_action()
            return "Ação cancelada por falta de um alvo válido."

        return self._resolve_and_execute_pending(pending, message)

    def _resolve_and_execute_pending(
        self,
        pending: PendingAction,
        spoken_target: str,
    ) -> str:
        resolver = self.file_reference_resolver
        tool = self.tool_registry.get(pending.tool_name)
        if resolver is None or tool is None:
            self._cancel_pending_action()
            return "Ação cancelada por falta de um alvo válido."
        match = resolver.resolve(spoken_target)
        if match.kind is FileMatchKind.NOT_FOUND or match.path is None:
            if match.kind is FileMatchKind.AMBIGUOUS:
                options = ", ".join(match.candidates[:3])
                self.pending_actions.set(
                    PendingAction(
                        tool_name=pending.tool_name,
                        original_request=pending.original_request,
                        missing_field=pending.missing_field,
                        resolved_arguments=pending.resolved_arguments,
                        candidate_targets=match.candidates[:3],
                        created_at=pending.created_at,
                        state=PendingActionState.WAITING_TARGET,
                        interaction_id=pending.interaction_id,
                    )
                )
                return f"Encontrei mais de uma opção: {options}. Qual delas?"
            return "Não encontrei um arquivo correspondente. Qual é o nome?"

        self.pending_actions.transition(
            PendingActionState.WAITING_TARGET_CONFIRMATION
        )
        if match.kind is not FileMatchKind.EXACT_LITERAL:
            request = ConfirmationRequest(
                action="confirm_file_target",
                target=match.path,
                risk=RiskLevel.LOW,
                prompt_text=f"Você quis dizer {match.path}?",
                purpose=ConfirmationPurpose.TARGET,
            )
            confirmation = self.target_confirmation
            result = (
                confirmation.confirm_result(request)
                if confirmation is not None
                else None
            )
            if result is None or not result.target_confirmed:
                self._cancel_pending_action()
                return "Cancelado."
        else:
            result = None

        arguments = dict(pending.resolved_arguments)
        arguments[pending.missing_field] = match.path
        self.pending_actions.transition(
            PendingActionState.WAITING_SECURITY_CONFIRMATION
        )
        self.pending_actions.transition(PendingActionState.READY_TO_EXECUTE)
        grant = None
        if result is not None and result.action_authorized:
            grant = self._grant_compound_authorization(
                tool, arguments, pending.interaction_id
            )
        execution_result = self._execute_tool(
            tool, arguments, grant, pending.interaction_id
        )
        self.pending_actions.clear()
        if execution_result.status == "success":
            self._remember_operational_context(
                tool.name, arguments, execution_result.content
            )
            return execution_result.content
        if execution_result.status in {"denied", "security_blocked"}:
            return (
                "A exclusão foi cancelada."
                if tool.name == "delete_file"
                else "A ação foi cancelada."
            )
        return execution_result.content

    def _grant_compound_authorization(
        self,
        tool: Any,
        arguments: dict[str, Any],
        interaction_id: str,
    ) -> AuthorizationGrant | None:
        safety_manager = getattr(self.tool_executor, "safety_manager", None)
        issue = getattr(
            safety_manager, "grant_explicit_authorization", None
        )
        if not callable(issue) or tool.risk < RiskLevel.MEDIUM:
            return None
        target = tool.get_target(**arguments)
        return issue(tool.name, target, tool.risk, interaction_id)

    def _cancel_pending_action(self) -> None:
        self.pending_actions.cancel()
        safety_manager = getattr(self.tool_executor, "safety_manager", None)
        invalidate = getattr(
            safety_manager, "invalidate_authorizations", None
        )
        if callable(invalidate):
            invalidate()

    def _system_context(self) -> list[dict[str, Any]]:
        memories = self.memory_store.get_all()
        lines = [self.BASE_SYSTEM_PROMPT]
        active = self.context_router.state.active
        lines.extend(
            [
                f"Current source context: {active.value}",
                "Use as ferramentas da fonte ativa quando o pedido ainda "
                "fizer sentido nela. Uma troca explícita do usuário tem "
                "prioridade.",
            ]
        )
        self._append_recent_context(lines, active)
        if memories:
            lines.extend(
                [
                    "Memórias persistentes armazenadas explicitamente "
                    "pelo usuário.",
                    "Use-as apenas como contexto. Não crie, edite ou "
                    "exclua memórias.",
                ]
            )
            lines.extend(
                f"- [{entry.category}] {entry.content} (id: {entry.id})"
                for entry in memories
            )
        return [{"role": "system", "content": "\n".join(lines)}]

    def _append_recent_context(
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

    @staticmethod
    def _tool_matches_context(name: str, active: ActiveContext) -> bool:
        if active is ActiveContext.OBSIDIAN:
            return name.startswith("obsidian_")
        if active is ActiveContext.FILESYSTEM:
            return name in {
                "list_files", "read_file", "create_file", "edit_file",
                "delete_file", "move_file",
            }
        return False

    def _route_tool_call(self, message: str, tool_call: Any) -> None:
        """Corrige apenas rotas equivalentes; não concede autorização."""
        active = self.context_router.state.active
        if active is ActiveContext.OBSIDIAN:
            if tool_call.name == "list_files":
                query = self._search_query(message)
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

    def _search_query(self, message: str) -> str:
        normalized = self.context_router.normalize(message)
        value = re.sub(
            r"\b(?:procure|pesquise|busque|encontre|as|os|minhas?|meus?|"
            r"notas?|anotacoes?|sobre|no|na|obsidian|vault|cofre|por|favor)\b",
            " ", normalized,
        )
        return " ".join(value.split())

    def _finalize_llm_response(
        self,
        content: str,
        messages: list[dict[str, Any]],
    ) -> str:
        validated = self.final_response_firewall.validate(content)
        if validated.accepted:
            return validated.text
        if self.config.llm_debug_enabled:
            print(
                "[llm] final response rejected "
                f"reason={validated.reason}"
            )
        # O retry recebe apenas o histórico e resultados já produzidos.
        # Não há schema de Tool, executor ou grant neste caminho.
        retry_messages = messages[:-1] if messages else messages
        retry = self.llm.generate_final(retry_messages)
        if retry.tool_calls:
            return self.SAFE_FALLBACK_RESPONSE
        retried = self.final_response_firewall.validate(retry.content)
        return (
            retried.text
            if retried.accepted
            else "Não consegui formular uma resposta segura. Tente novamente."
        )

    def _runtime_info_response(self, message: str) -> str | None:
        normalized = self.confirmation_interpreter.normalize(message)
        asks_runtime = any(
            phrase in normalized
            for phrase in (
                "qual e seu timeout",
                "qual seu timeout",
                "quanto tempo eu tenho para responder",
                "quanto tempo para responder",
                "voce esta em game mode",
                "qual o game mode",
            )
        )
        if not asks_runtime:
            return None
        tool = self.tool_registry.get("get_runtime_info")
        if tool is None:
            return None
        return self._execute_tool(tool, {}).content

    def _note_search_suggestion(self, query: str) -> str | None:
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

    def _is_ambiguous_note_cleanup(self, message: str) -> bool:
        normalized = self.confirmation_interpreter.normalize(message)
        mentions_notes = re.search(r"\bnotas?\b", normalized) is not None
        note_cleanup = mentions_notes and any(
            phrase in normalized
            for phrase in (
                "limpa", "limpe", "limpar", "limpada", "apaga", "remove",
                "exclui",
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
