"""Coordenação dos caminhos determinísticos do Obsidian."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from jarvis.config import JarvisConfig
from jarvis.context.active import ActiveContext, ContextRouter
from jarvis.context.compound_actions import CompoundActionPlanner
from jarvis.context.note_reference_resolver import NoteMatchKind, NoteReferenceResolver
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.obsidian_commands import ObsidianCommandParser
from jarvis.security.tool_intent import ToolIntentGuard
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.validator import ToolArgumentValidator


ExecuteTool = Callable[[Any, dict[str, Any]], ToolExecutionResult]
RememberOperationalContext = Callable[[str, dict[str, Any], str], None]
ExecuteCompoundFollowUp = Callable[..., Any]


class ObsidianCoordinator:
    """Mantém parsing/fast paths do Obsidian fora do Harness."""

    def __init__(
        self,
        config: JarvisConfig,
        tool_registry: ToolRegistry,
        argument_validator: ToolArgumentValidator,
        intent_guard: ToolIntentGuard,
        memory: ConversationMemory,
        note_reference_resolver: NoteReferenceResolver | None,
        context_router: ContextRouter,
        command_parser: ObsidianCommandParser,
        compound_action_planner: CompoundActionPlanner,
        execute_tool: ExecuteTool,
        execute_compound_follow_up: ExecuteCompoundFollowUp,
        remember_operational_context: RememberOperationalContext,
    ) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.argument_validator = argument_validator
        self.intent_guard = intent_guard
        self.memory = memory
        self.note_reference_resolver = note_reference_resolver
        self.context_router = context_router
        self.command_parser = command_parser
        self.compound_action_planner = compound_action_planner
        self.execute_tool = execute_tool
        self.execute_compound_follow_up = execute_compound_follow_up
        self.remember_operational_context = remember_operational_context

    def available(self) -> bool:
        vault = self.config.obsidian_vault_path
        return bool(
            self.config.obsidian_enabled
            and vault is not None
            and vault.is_dir()
            and self.config.obsidian_read_enabled
            and self.tool_registry.get("obsidian_search_notes") is not None
            and self.tool_registry.get("obsidian_read_note") is not None
        )

    def write_available(self) -> bool:
        return bool(
            self.config.obsidian_write_enabled
            and self.tool_registry.get("obsidian_append_note") is not None
        )

    def requests_write(self, message: str) -> bool:
        normalized = self.context_router.normalize(message)
        return re.search(
            r"\b(?:adicione|adiciona|adicionar|edite|edita|editar|"
            r"coloque|coloca|colocar|inserir|insira|acrescente|"
            r"anote|anota|anotar|escreva|escrever|registre|registrar)\b",
            normalized,
        ) is not None

    def fast_response(self, message: str) -> str | None:
        """Execute high-confidence Obsidian commands through real Tools."""
        if self.context_router.state.active is not ActiveContext.OBSIDIAN:
            return None
        command = self.command_parser.parse(message)
        if command is None:
            if self.command_parser.looks_like_create_without_title(message):
                return "Qual nome você quer dar à nota?"
            return None

        if command.action in {"create", "append"} and not self.write_available():
            return "A escrita no Obsidian está desabilitada ou indisponível."

        if command.action == "list":
            tool = self.tool_registry.get("obsidian_list_notes")
            if tool is None:
                return None
            arguments: dict[str, Any] = {}
            valid, error = self.argument_validator.validate(tool, arguments)
            if not valid:
                return f"Não consegui preparar a ação: {error}"
            intent = self.intent_guard.validate(message, tool, arguments)
            if not intent.allowed:
                return intent.response or "Não consegui validar essa ação."
            self.memory.add_user_message(message)
            result = self.execute_tool(tool, arguments)
            if result.status != "success":
                self.memory.add_assistant_message(result.content)
                return result.content
            self.remember_operational_context(tool.name, arguments, result.content)
            notes = [
                line.strip()
                for line in result.content.splitlines()
                if line.strip()
            ]
            response = (
                "Nenhuma nota encontrada."
                if not notes
                else "Aqui estão suas notas:\n"
                + "\n".join(f"- {note}" for note in notes)
            )
            self.memory.add_assistant_message(response)
            return response

        if command.action == "open" and command.reference is not None:
            return None

        if command.action == "create":
            tool = self.tool_registry.get("obsidian_create_note")
            if tool is None or not command.reference:
                return None
            try:
                path = tool.vault.normalize_note_path(command.reference)
            except (AttributeError, PermissionError, ValueError) as error:
                return f"Não consegui usar esse título de nota: {error}"
            arguments = {"path": path, "content": command.content}
            self.memory.add_user_message(message)
            result = self.execute_tool(tool, arguments)
            if result.status != "success":
                self.memory.add_assistant_message(result.content)
                return result.content
            self.remember_operational_context(tool.name, arguments, result.content)
            response = (
                f'Pronto. Criei a nota "{path}" e gravei o conteúdo.'
                if command.content
                else f'Pronto. Criei a nota "{path}".'
            )
            self.memory.add_assistant_message(response)
            return response

        reference = command.reference or "essa nota"
        match = (
            self.note_reference_resolver.resolve(reference)
            if self.note_reference_resolver is not None
            else None
        )
        if match is None:
            return None
        if match.kind is NoteMatchKind.AMBIGUOUS:
            return "Qual nota você quis dizer? " + ", ".join(match.candidates)
        if match.kind is NoteMatchKind.NOT_FOUND or match.canonical_path is None:
            return (
                f'Não encontrei a nota "{command.reference or "anterior"}" '
                "no Obsidian."
            )
        path = match.canonical_path

        if command.action == "append":
            action_names = ("obsidian_append_note",)
        elif command.action == "open":
            action_names = ("obsidian_open_note",)
        elif command.action == "read":
            action_names = ("obsidian_read_note",)
        elif command.action == "open_read":
            action_names = ("obsidian_open_note", "obsidian_read_note")
        else:
            return None

        self.memory.add_user_message(message)
        last_result: ToolExecutionResult | None = None
        for tool_name in action_names:
            tool = self.tool_registry.get(tool_name)
            if tool is None:
                return None
            arguments: dict[str, Any] = {"path": path}
            if tool_name == "obsidian_append_note":
                arguments["content"] = command.content
            valid, error = self.argument_validator.validate(tool, arguments)
            if not valid:
                response = f"Não consegui preparar a ação: {error}"
                self.memory.add_assistant_message(response)
                return response
            intent = self.intent_guard.validate(message, tool, arguments)
            if not intent.allowed:
                response = intent.response or "Não consegui validar essa ação."
                self.memory.add_assistant_message(response)
                return response
            last_result = self.execute_tool(tool, arguments)
            if last_result.status != "success":
                self.memory.add_assistant_message(last_result.content)
                return last_result.content
            self.remember_operational_context(
                tool.name, arguments, last_result.content
            )

        if command.action == "append":
            follow_up = self.execute_compound_follow_up(
                message,
                completed_tool="obsidian_append_note",
                completed_arguments={
                    "path": path,
                    "content": command.content,
                },
                already_requested=set(action_names),
            )
            if follow_up is not None:
                _name, _arguments, follow_result = follow_up
                if follow_result.status != "success":
                    self.memory.add_assistant_message(follow_result.content)
                    return follow_result.content
                response = follow_result.content.strip() or "A nota está vazia."
            else:
                response = (
                    last_result.content
                    if last_result is not None
                    else "Conteúdo adicionado à nota."
                )
        elif command.action in {"read", "open_read"}:
            response = (
                last_result.content.strip() if last_result is not None else ""
            ) or "A nota está vazia."
        else:
            response = f'Pronto. Abri a nota "{path}" no Obsidian.'
        self.memory.add_assistant_message(response)
        return response

    def compound_fast_response(self, message: str) -> str | None:
        resolver = self.note_reference_resolver
        if resolver is None or not self.available():
            return None
        reference = self.compound_action_planner.explicit_open_read_reference(
            message
        )
        if reference is None:
            return None
        match = resolver.resolve(reference)
        accepted = match.kind in {
            NoteMatchKind.EXACT_LITERAL,
            NoteMatchKind.NORMALIZED,
            NoteMatchKind.CONTEXT,
        }
        if match.kind is NoteMatchKind.FUZZY and (match.score or 0.0) >= 0.82:
            accepted = True
        if not accepted or match.canonical_path is None:
            return None
        path = match.canonical_path
        open_tool = self.tool_registry.get("obsidian_open_note")
        read_tool = self.tool_registry.get("obsidian_read_note")
        if open_tool is None or read_tool is None:
            return None
        arguments = {"path": path}
        for tool in (open_tool, read_tool):
            valid, _error = self.argument_validator.validate(tool, arguments)
            if not valid:
                return None
            intent = self.intent_guard.validate(message, tool, arguments)
            if not intent.allowed:
                return None

        self.memory.add_user_message(message)
        opened = self.execute_tool(open_tool, arguments)
        if opened.status != "success":
            self.memory.add_assistant_message(opened.content)
            return opened.content
        self.remember_operational_context(
            open_tool.name, arguments, opened.content
        )
        read = self.execute_tool(read_tool, arguments)
        if read.status != "success":
            self.memory.add_assistant_message(read.content)
            return read.content
        self.remember_operational_context(
            read_tool.name, arguments, read.content
        )
        response = read.content.strip() or "A nota está vazia."
        self.memory.add_assistant_message(response)
        return response
