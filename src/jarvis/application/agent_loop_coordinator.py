"""Coordena o agent loop LLM -> Tools sem executar fora do Core."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jarvis.config import JarvisConfig
from jarvis.context.active import ContextRouter
from jarvis.context.pending_action import PendingAction, PendingActionContext, PendingActionState
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.llm.tool_schema import tool_to_ollama_schema
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.tool_selection import ToolSelectionRouter
from jarvis.security.authorization import AuthorizationGrant
from jarvis.security.content_isolation import format_untrusted_data
from jarvis.security.risk import RiskLevel
from jarvis.security.tool_intent import ToolIntentGuard
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.validator import ToolArgumentValidator

from jarvis.application.response_coordinator import ResponseCoordinator
from jarvis.application.tool_execution_coordinator import ToolExecutionCoordinator
from jarvis.application.turn_policy import TurnKind


GenerateLLM = Callable[[list[dict[str, Any]], list[dict[str, Any]]], LLMResponse]
ExecuteTool = Callable[
    [Any, dict[str, Any], AuthorizationGrant | None, str | None],
    ToolExecutionResult,
]


class AgentLoopCoordinator:
    """Executa o ciclo de raciocínio/ferramentas mantendo a execução no Core.

    Este coordenador decide quais Tools expor ao LLM, processa Tool Calls,
    resolve alvos e retries, e monta o histórico do turno. A execução real
    continua entrando pelo callback ``execute_tool``, que no Harness passa pelo
    ToolExecutor, SafetyManager, policy e audit existentes.
    """

    def __init__(
        self,
        config: JarvisConfig,
        llm: LLMClient,
        memory: ConversationMemory,
        context_router: ContextRouter,
        tool_registry: ToolRegistry,
        tool_selection_router: ToolSelectionRouter,
        argument_validator: ToolArgumentValidator,
        intent_guard: ToolIntentGuard,
        pending_actions: PendingActionContext,
        tool_execution_coordinator: ToolExecutionCoordinator,
        response_coordinator: ResponseCoordinator,
        generate_llm: GenerateLLM,
        execute_tool: ExecuteTool,
    ) -> None:
        self.config = config
        self.llm = llm
        self.memory = memory
        self.context_router = context_router
        self.tool_registry = tool_registry
        self.tool_selection_router = tool_selection_router
        self.argument_validator = argument_validator
        self.intent_guard = intent_guard
        self.pending_actions = pending_actions
        self.tool_execution_coordinator = tool_execution_coordinator
        self.response_coordinator = response_coordinator
        self.generate_llm = generate_llm
        self.execute_tool = execute_tool

    def process(
        self,
        message: str,
        response_mode: str,
        turn_kind: TurnKind,
    ) -> str:
        active_context = self.context_router.state.active
        available_tools = []
        if turn_kind is TurnKind.EFFECT_REQUEST:
            available_tools = self.tool_selection_router.select(
                message,
                active_context,
                self.tool_registry.list_tools(),
            )
        available_tools.sort(
            key=lambda tool: not self.response_coordinator.tool_matches_context(
                tool.name, active_context
            )
        )
        available_tools = [tool for tool in available_tools if tool.name not in {
            "google_calendar_list_events", "google_calendar_get_event",
            "google_calendar_check_availability",
        } and not tool.name.startswith(('google_drive_', 'google_docs_', 'app_catalog_', 'organization_'))]
        tools = [tool_to_ollama_schema(tool) for tool in available_tools]

        self.memory.add_user_message(message)
        messages: list[dict[str, Any]] = self.response_coordinator.system_context(
            response_mode, user_message=message
        )
        if turn_kind is TurnKind.ADVISORY:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Este turno é consultivo. Converse, avalie ou planeje sem "
                        "executar efeitos reais. Não alegue que realizou ações."
                    ),
                }
            )
        messages.extend(self.memory.get_history())
        successful_tool_names: set[str] = set()
        tool_retry_used = False

        for _ in range(self.config.max_tool_iterations):
            response = self.generate_llm(messages, tools)
            if turn_kind is TurnKind.ADVISORY and response.tool_calls:
                final_response = (
                    "Este turno é consultivo. Posso avaliar ou planejar isso, "
                    "mas não vou executar ações sem um pedido explícito."
                )
                self.memory.add_assistant_message(final_response)
                return final_response
            for tool_call in response.tool_calls:
                self.response_coordinator.route_tool_call(message, tool_call)
            requested_tool_names = {
                tool_call.name for tool_call in response.tool_calls
            }
            if response.tool_calls:
                # Conteúdo textual adjacente a uma tool call é protocolo
                # interno e nunca é candidato a resposta final.
                response.content = ""
            messages.append(response.to_message())

            if not response.tool_calls:
                action_requested = (
                    turn_kind is TurnKind.EFFECT_REQUEST
                    and self.tool_selection_router.looks_like_action(
                        message, self.context_router.state.active
                    )
                )
                if (
                    action_requested
                    and tools
                    and not successful_tool_names
                    and not tool_retry_used
                ):
                    tool_retry_used = True
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "O pedido do usuário parece exigir uma ação real. "
                                "Ignore qualquer narração de execução da resposta "
                                "anterior. Se houver uma Tool adequada e a intenção "
                                "estiver clara, faça a chamada agora. Não exija uma "
                                "frase exata. Se faltar um alvo necessário, peça "
                                "somente esse esclarecimento."
                            ),
                        }
                    )
                    continue

                if action_requested and not successful_tool_names:
                    final_response = (
                        "Entendi que você quer uma ação real, mas não consegui "
                        "determinar uma Tool segura para executá-la nessa tentativa. "
                        "Se estiver faltando um alvo, diga qual arquivo, nota ou "
                        "item você quer usar."
                    )
                else:
                    final_response = self.response_coordinator.finalize_llm_response(
                        response.content,
                        messages,
                        tool_executed=bool(successful_tool_names),
                        successful_tool_names=successful_tool_names,
                        allow_retry=turn_kind is TurnKind.EFFECT_REQUEST,
                    )
                self.memory.add_assistant_message(final_response)
                return final_response

            for tool_call in response.tool_calls:
                result = self._process_tool_call(
                    message,
                    tool_call,
                    requested_tool_names,
                    successful_tool_names,
                    messages,
                )
                if result is not None:
                    return result

        final_response = "Limite de chamadas de ferramentas atingido."
        self.memory.add_assistant_message(final_response)
        return final_response

    def _process_tool_call(
        self,
        message: str,
        tool_call: ToolCallRequest,
        requested_tool_names: set[str],
        successful_tool_names: set[str],
        messages: list[dict[str, Any]],
    ) -> str | None:
        tool = self.tool_registry.get(tool_call.name)
        if tool_call.name.startswith(('app_catalog_', 'organization_')):
            return 'Aplicativos exigem intenção local inequívoca e identidade selecionada; nenhuma operação executada.'
        if tool_call.name.startswith(('google_drive_', 'google_docs_')) or tool_call.name in {
            "google_calendar_list_events", "google_calendar_get_event",
            "google_calendar_check_availability",
        }:
            return "Consulta de agenda exige uma intenção local inequívoca; nenhuma consulta executada."
        resolved_arguments = dict(tool_call.arguments)
        execution_result: ToolExecutionResult | None = None

        if tool is None:
            tool_result = (
                f"Tool não permitida ou inexistente: '{tool_call.name}'."
            )
        else:
            valid, error = self.argument_validator.validate(
                tool,
                tool_call.arguments,
            )
            if not valid:
                tool_result = f"Chamada de Tool rejeitada: {error}"
            else:
                early_response = self._validate_intent_or_prepare_pending(
                    message,
                    tool,
                    tool_call.arguments,
                )
                if early_response is not None:
                    return early_response

                (
                    resolved_arguments,
                    resolution_response,
                    prepared_grant,
                    interaction_id,
                ) = self.tool_execution_coordinator.resolve_note_reference(
                    tool,
                    tool_call.arguments,
                )
                if resolution_response is None and prepared_grant is None:
                    (
                        resolved_arguments,
                        resolution_response,
                        prepared_grant,
                        interaction_id,
                    ) = self.tool_execution_coordinator.resolve_file_reference(
                        tool, resolved_arguments
                    )
                if resolution_response is not None:
                    self.memory.add_assistant_message(resolution_response)
                    return resolution_response

                execution_result = self.execute_tool(
                    tool,
                    resolved_arguments,
                    prepared_grant,
                    interaction_id,
                )
                tool_result = execution_result.content
                early_response = self._handle_execution_result(
                    message,
                    tool,
                    resolved_arguments,
                    execution_result,
                    successful_tool_names,
                )
                if early_response is not None:
                    return early_response

        messages.append(
            {
                "role": "tool",
                "tool_name": tool_call.name,
                "content": format_untrusted_data(
                    f"tool_result:{tool_call.name}",
                    tool_result,
                ),
            }
        )
        if (
            tool is not None
            and execution_result is not None
            and execution_result.status == "success"
        ):
            follow_up = self.tool_execution_coordinator.execute_compound_follow_up(
                message,
                completed_tool=tool.name,
                completed_arguments=resolved_arguments,
                already_requested=requested_tool_names,
            )
            if follow_up is not None:
                follow_name, follow_arguments, follow_result = follow_up
                messages.append(
                    LLMResponse(
                        content="",
                        tool_calls=[
                            ToolCallRequest(follow_name, follow_arguments)
                        ],
                    ).to_message()
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": follow_name,
                        "content": format_untrusted_data(
                            f"tool_result:{follow_name}",
                            follow_result.content,
                        ),
                    }
                )
                requested_tool_names.add(follow_name)
        return None

    def _validate_intent_or_prepare_pending(
        self,
        message: str,
        tool: Any,
        arguments: dict[str, Any],
    ) -> str | None:
        intent = self.intent_guard.validate(message, tool, arguments)
        if intent.allowed:
            return None

        clarification = intent.response or (
            "Informe um alvo explícito para essa ação."
        )
        missing_field = self.tool_execution_coordinator.missing_target_field(
            arguments
        )
        if missing_field is not None:
            remaining = dict(arguments)
            state = PendingActionState.WAITING_TARGET
            if intent.requires_intent_confirmation:
                state = PendingActionState.WAITING_INTENT_CONFIRMATION
            else:
                remaining.pop(missing_field, None)
            self.pending_actions.set(
                PendingAction(
                    tool_name=tool.name,
                    original_request=message,
                    missing_field=missing_field,
                    resolved_arguments=remaining,
                    state=state,
                )
            )
        self.memory.add_assistant_message(clarification)
        return clarification

    def _handle_execution_result(
        self,
        message: str,
        tool: Any,
        resolved_arguments: dict[str, Any],
        execution_result: ToolExecutionResult,
        successful_tool_names: set[str],
    ) -> str | None:
        tool_result = execution_result.content
        if execution_result.status == "success":
            successful_tool_names.add(tool.name)
        elif execution_result.status == "confirmation_required":
            self.memory.add_assistant_message(tool_result)
            return tool_result
        elif (
            tool.name.startswith("google_calendar_")
            and execution_result.status != "success"
        ):
            self.memory.add_assistant_message(tool_result)
            return tool_result
        elif (
            tool.name in {
                "desktop_focus_window",
                "desktop_open_application",
                "desktop_move_pointer_to_window",
                "desktop_press_escape",
                "desktop_close_window",
                "vision_observe_screen_change",
            }
            and execution_result.status != "success"
        ):
            self.memory.add_assistant_message(tool_result)
            return tool_result
        elif tool.name in {
            "obsidian_create_note",
            "obsidian_append_note",
            "obsidian_open_note",
            "obsidian_read_note",
            "obsidian_delete_note",
        }:
            self.memory.add_assistant_message(tool_result)
            return tool_result

        if (
            tool.name == "obsidian_search_notes"
            and execution_result.status == "success"
            and tool_result == "Nenhuma nota encontrada."
        ):
            suggestion = self.response_coordinator.note_search_suggestion(
                str(resolved_arguments.get("query", ""))
            )
            if suggestion is not None:
                clarification = f"Você quis dizer '{suggestion}'?"
                self.pending_actions.set(
                    PendingAction(
                        tool_name=tool.name,
                        original_request=message,
                        missing_field="query",
                        resolved_arguments={"query": suggestion},
                        state=PendingActionState.WAITING_TARGET_CONFIRMATION,
                    )
                )
                self.memory.add_assistant_message(clarification)
                return clarification

        if execution_result.status == "success":
            self.tool_execution_coordinator.remember_operational_context(
                tool.name,
                resolved_arguments,
                execution_result.content,
            )
        if (
            tool.risk >= RiskLevel.EXTERNAL_EFFECT
            and execution_result.status == "not_found"
        ):
            not_found = "Arquivo não encontrado."
            self.memory.add_assistant_message(not_found)
            return not_found
        if (
            tool.risk >= RiskLevel.SENSITIVE
            and execution_result.status in {"denied", "security_blocked"}
        ):
            cancellation = (
                "A exclusão foi cancelada."
                if tool.name == "delete_file"
                else "A ação foi cancelada."
            )
            self.memory.add_assistant_message(cancellation)
            return cancellation
        return None
