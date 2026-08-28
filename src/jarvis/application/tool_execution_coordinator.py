"""Orquestra resolução de alvos, ações pendentes e follow-ups de Tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from jarvis.context.active import ActiveContext, ContextRouter
from jarvis.context.compound_actions import CompoundActionPlanner
from jarvis.context.file_reference_resolver import FileMatchKind, FileReferenceResolver
from jarvis.context.note_reference_resolver import NoteMatchKind, NoteReferenceResolver
from jarvis.context.pending_action import PendingAction, PendingActionContext, PendingActionState
from jarvis.context.recent import RecentContext
from jarvis.runtime.cancellation import CancellationInterpreter
from jarvis.security.authorization import AuthorizationGrant
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.confirmation_intent import ConfirmationIntent, ConfirmationInterpreter
from jarvis.security.confirmation_request import ConfirmationPurpose, ConfirmationRequest
from jarvis.security.risk import RiskLevel
from jarvis.security.tool_intent import ToolIntentGuard
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.validator import ToolArgumentValidator


ExecuteTool = Callable[
    [Any, dict[str, Any], AuthorizationGrant | None, str | None],
    ToolExecutionResult,
]


class ToolExecutionCoordinator:
    """Coordena ações em torno da execução sem contornar o ToolExecutor.

    A execução primitiva continua sendo fornecida pelo Core. Este componente
    apenas resolve referências, confirma alvos, gerencia PendingAction e planeja
    follow-ups seguros.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        argument_validator: ToolArgumentValidator,
        intent_guard: ToolIntentGuard,
        compound_action_planner: CompoundActionPlanner,
        file_reference_resolver: FileReferenceResolver | None,
        note_reference_resolver: NoteReferenceResolver | None,
        target_confirmation: ConfirmationProvider | None,
        pending_actions: PendingActionContext,
        cancellation_interpreter: CancellationInterpreter,
        confirmation_interpreter: ConfirmationInterpreter,
        context_router: ContextRouter,
        recent_context: RecentContext,
        execute_tool: ExecuteTool,
    ) -> None:
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.argument_validator = argument_validator
        self.intent_guard = intent_guard
        self.compound_action_planner = compound_action_planner
        self.file_reference_resolver = file_reference_resolver
        self.note_reference_resolver = note_reference_resolver
        self.target_confirmation = target_confirmation
        self.pending_actions = pending_actions
        self.cancellation_interpreter = cancellation_interpreter
        self.confirmation_interpreter = confirmation_interpreter
        self.context_router = context_router
        self.recent_context = recent_context
        self.execute_tool = execute_tool

    def execute_compound_follow_up(
        self,
        message: str,
        *,
        completed_tool: str,
        completed_arguments: dict[str, Any],
        already_requested: set[str],
    ) -> tuple[str, dict[str, Any], ToolExecutionResult] | None:
        planned = self.compound_action_planner.follow_up(
            message,
            completed_tool=completed_tool,
            completed_arguments=completed_arguments,
            already_requested=already_requested,
        )
        if planned is None:
            return None
        tool = self.tool_registry.get(planned.name)
        if tool is None or tool.risk >= RiskLevel.MEDIUM:
            return None
        valid, _error = self.argument_validator.validate(tool, planned.arguments)
        if not valid:
            return None
        intent = self.intent_guard.validate(message, tool, planned.arguments)
        if not intent.allowed:
            return None
        result = self.execute_tool(tool, planned.arguments, None, None)
        if result.status == "success":
            self.remember_operational_context(
                tool.name, planned.arguments, result.content
            )
        return tool.name, dict(planned.arguments), result

    def resolve_file_reference(
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
            or match.kind is FileMatchKind.FUZZY
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
                self.cancel_pending_action()
                return arguments, "Cancelado.", None, None
            if result.action_authorized:
                resolved = dict(arguments)
                resolved["path"] = match.path
                interaction_id = str(uuid4())
                grant = self.grant_compound_authorization(
                    tool, resolved, interaction_id
                )
                return resolved, None, grant, interaction_id
        resolved = dict(arguments)
        resolved["path"] = match.path
        return resolved, None, None, None

    def remember_operational_context(
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
        if tool_name in {"obsidian_list_notes", "obsidian_search_notes"}:
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
            self.recent_context.set("last_search_results", tuple(listed_files))
            self.recent_context.set("last_file_results", listed_files)
        else:
            self.recent_context.set("last_file", path)
            self.recent_context.set("last_active_file", path)

    def resolve_note_reference(
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
            self.cancel_pending_action()
            return arguments, "Cancelado.", None, None
        if result.action_authorized:
            interaction_id = str(uuid4())
            grant = self.grant_compound_authorization(
                tool, resolved, interaction_id
            )
            return resolved, None, grant, interaction_id
        return resolved, None, None, None

    @staticmethod
    def missing_target_field(arguments: dict[str, Any]) -> str | None:
        for field in ("path", "target", "source"):
            if field in arguments:
                return field
        return None

    def process_pending_message(self, message: str) -> str | None:
        pending = self.pending_actions.get()
        if pending is None:
            return None
        if self.cancellation_interpreter.is_cancellation(message):
            self.cancel_pending_action()
            return "Cancelado."
        if (
            pending.missing_field == "query"
            and pending.state is PendingActionState.WAITING_TARGET_CONFIRMATION
        ):
            intent = self.confirmation_interpreter.interpret(message)
            if intent is not ConfirmationIntent.CONFIRM:
                self.cancel_pending_action()
                return "Cancelado."
            tool = self.tool_registry.get(pending.tool_name)
            if tool is None:
                self.cancel_pending_action()
                return "Cancelado."
            arguments = dict(pending.resolved_arguments)
            self.pending_actions.clear()
            result = self.execute_tool(tool, arguments, None, None)
            if result.status == "success":
                self.remember_operational_context(
                    tool.name, arguments, result.content
                )
            return result.content
        if pending.state is PendingActionState.WAITING_INTENT_CONFIRMATION:
            intent = self.confirmation_interpreter.interpret(message)
            if intent is not ConfirmationIntent.CONFIRM:
                self.cancel_pending_action()
                return "Cancelado."
            spoken_target = pending.resolved_arguments.get(pending.missing_field)
            if not isinstance(spoken_target, str):
                self.cancel_pending_action()
                return "Cancelado."
            return self.resolve_and_execute_pending(pending, spoken_target)
        if pending.missing_field != "path":
            self.cancel_pending_action()
            return "Ação cancelada por falta de um alvo válido."
        resolver = self.file_reference_resolver
        tool = self.tool_registry.get(pending.tool_name)
        if resolver is None or tool is None:
            self.cancel_pending_action()
            return "Ação cancelada por falta de um alvo válido."

        return self.resolve_and_execute_pending(pending, message)

    def process_pending_cancellation(self, message: str) -> str | None:
        if (
            self.pending_actions.has_pending()
            and self.cancellation_interpreter.is_cancellation(message)
        ):
            self.cancel_pending_action()
            return "Cancelado."
        return None

    def resolve_and_execute_pending(
        self,
        pending: PendingAction,
        spoken_target: str,
    ) -> str:
        resolver = self.file_reference_resolver
        tool = self.tool_registry.get(pending.tool_name)
        if resolver is None or tool is None:
            self.cancel_pending_action()
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
                self.cancel_pending_action()
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
            grant = self.grant_compound_authorization(
                tool, arguments, pending.interaction_id
            )
        execution_result = self.execute_tool(
            tool, arguments, grant, pending.interaction_id
        )
        self.pending_actions.clear()
        if execution_result.status == "success":
            self.remember_operational_context(
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

    def grant_compound_authorization(
        self,
        tool: Any,
        arguments: dict[str, Any],
        interaction_id: str,
    ) -> AuthorizationGrant | None:
        safety_manager = getattr(self.tool_executor, "safety_manager", None)
        issue = getattr(safety_manager, "grant_explicit_authorization", None)
        if not callable(issue) or tool.risk < RiskLevel.MEDIUM:
            return None
        target = tool.get_target(**arguments)
        return issue(tool.name, target, tool.risk, interaction_id)

    def cancel_pending_action(self) -> None:
        self.pending_actions.cancel()
        safety_manager = getattr(self.tool_executor, "safety_manager", None)
        invalidate = getattr(safety_manager, "invalidate_authorizations", None)
        if callable(invalidate):
            invalidate()
