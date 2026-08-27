from typing import Any
from uuid import uuid4
from difflib import SequenceMatcher
from pathlib import Path
import re
import time

from jarvis.security.authorization import AuthorizationGrant

from jarvis.config import JarvisConfig
from jarvis.context.recent import RecentContext
from jarvis.context.active import ActiveContext, ContextRouter
from jarvis.context.compound_actions import CompoundActionPlanner
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
from jarvis.llm.grounding import ExecutionClaimGuard
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.llm.tool_schema import tool_to_ollama_schema
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.memory.store import MemoryStore
from jarvis.persona import PersonaProfile
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.cancellation import CancellationInterpreter
from jarvis.runtime.fast_path import (
    FastPathRouter,
    format_datetime_tool_result,
)
from jarvis.runtime.tool_selection import ToolSelectionRouter
from jarvis.runtime.obsidian_commands import ObsidianCommandParser
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
from jarvis.skills.executor import SkillExecutor
from jarvis.skills.learner import SkillLearner
from jarvis.skills.matcher import SkillMatcher
from jarvis.skills.mirror import ObsidianSkillMirror
from jarvis.skills.models import ToolTraceStep
from jarvis.skills.store import InMemorySkillStore, SkillStore
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.validator import ToolArgumentValidator


class Harness:
    SAFE_FALLBACK_RESPONSE = (
        "Não consegui concluir essa resposta. Tente novamente."
    )
    BASE_SYSTEM_PROMPT = (
        "Você é Jarvis, um assistente pessoal local."
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
        skill_store: SkillStore | None = None,
        skill_mirror: ObsidianSkillMirror | None = None,
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
        self.fast_path_router = FastPathRouter()
        self.compound_action_planner = CompoundActionPlanner()
        self.tool_selection_router = ToolSelectionRouter()
        self.obsidian_command_parser = ObsidianCommandParser()
        self.skill_store = skill_store or InMemorySkillStore()
        self.skill_learner = SkillLearner(tool_registry)
        self.skill_matcher = SkillMatcher()
        self.skill_executor = SkillExecutor(
            tool_registry, tool_executor, execute_step=self._execute_tool
        )
        self.skill_mirror = skill_mirror
        self._current_tool_trace: list[ToolTraceStep] = []
        self._current_trace_request = ""
        self.last_tool_trace: list[ToolTraceStep] = []
        self.last_tool_trace_request = ""
        self.final_response_firewall = FinalResponseFirewall()
        self.execution_claim_guard = ExecutionClaimGuard()
        self.doctor = JarvisDoctor(
            self.config,
            self.resource_manager,
            self.memory_store,
            self.tool_registry,
            self.skill_store,
        )
        self.last_latency: dict[str, float | int] = {
            "harness_seconds": 0.0,
            "llm_seconds": 0.0,
            "llm_calls": 0,
            "tool_seconds": 0.0,
            "tool_calls": 0,
        }
        self._latency_turn_started_at: float | None = None

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
        response_mode: str = "text",
    ) -> str:
        stripped_message = mensagem.strip()
        command = stripped_message.lower()

        if command == "/doctor":
            return (
                self.doctor.voice_summary()
                if response_mode == "voice"
                else self.doctor.report()
            )

        if command == "/config" or command.startswith("/config "):
            return self._process_config_command(stripped_message)

        if command == "/latency":
            return self._format_latency_report()

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

        if command == "/skills":
            return self._format_skills()

        if command == "/skill" or command.startswith("/skill "):
            return self._process_skill_command(stripped_message)

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

        self._begin_tool_trace(mensagem)
        self._start_latency_turn()
        if not manage_runtime:
            try:
                return self._process_active_message(mensagem, response_mode)
            finally:
                self._finish_latency_turn()

        self.resource_manager.activate()
        try:
            return self._process_active_message(mensagem, response_mode)
        finally:
            self._finish_latency_turn()
            self.resource_manager.idle()

    def get_last_latency(self) -> dict[str, float | int]:
        return dict(self.last_latency)

    def _start_latency_turn(self) -> None:
        self.last_latency = {
            "harness_seconds": 0.0,
            "llm_seconds": 0.0,
            "llm_calls": 0,
            "tool_seconds": 0.0,
            "tool_calls": 0,
        }
        self._latency_turn_started_at = time.perf_counter()

    def _finish_latency_turn(self) -> None:
        started = self._latency_turn_started_at
        if started is not None:
            self.last_latency["harness_seconds"] = (
                time.perf_counter() - started
            )
        self._latency_turn_started_at = None
        if self._current_tool_trace:
            self.last_tool_trace = list(self._current_tool_trace)
            self.last_tool_trace_request = self._current_trace_request
        self._current_tool_trace = []
        self._current_trace_request = ""

    def _format_latency_report(self) -> str:
        latency = self.last_latency
        if not latency or float(latency.get("harness_seconds", 0.0)) <= 0:
            return "Ainda não há uma interação medida."
        tool_seconds = float(latency["tool_seconds"])
        tool_calls = int(latency["tool_calls"])
        tool_time = self._format_short_duration(tool_seconds, tool_calls)
        return (
            "Última interação: "
            f"Harness {float(latency['harness_seconds']):.2f}s | "
            f"LLM {float(latency['llm_seconds']):.2f}s "
            f"({int(latency['llm_calls'])} chamada(s)) | "
            f"Tools {tool_time} ({tool_calls} chamada(s))."
        )

    @staticmethod
    def _format_short_duration(seconds: float, calls: int) -> str:
        if calls > 0 and 0.0 <= seconds < 0.01:
            return f"{seconds * 1000:.1f}ms"
        return f"{seconds:.2f}s"

    def has_pending_action(self) -> bool:
        return self.pending_actions.has_pending()

    def _process_active_message(
        self, mensagem: str, response_mode: str = "text"
    ) -> str:
        natural_doctor = self._natural_doctor_response(
            mensagem, response_mode=response_mode
        )
        if natural_doctor is not None:
            return natural_doctor

        if self._natural_skill_list_requested(mensagem):
            return self._natural_skill_list_response()
        natural_skill_run = self._natural_skill_run_name(mensagem)
        if natural_skill_run is not None:
            return self._run_skill(natural_skill_run)
        natural_skill_name = self._natural_skill_learning_name(mensagem)
        if natural_skill_name is not None:
            return self._learn_last_skill(natural_skill_name)
        skill_control_fallback = self._skill_control_fallback(mensagem)
        if skill_control_fallback is not None:
            return skill_control_fallback

        if self._is_ambiguous_note_cleanup(mensagem):
            self._cancel_pending_action()
            return (
                "Posso listar as notas que parecem ser de teste e você "
                "escolhe quais deseja revisar ou remover. Qual escopo devo usar?"
            )
        fast_response = self._fast_path_response(mensagem)
        if fast_response is not None:
            return fast_response
        learned_response = self._learned_skill_response(mensagem)
        if learned_response is not None:
            return learned_response
        previous_context = self.context_router.state.active
        routed_context = self.context_router.update(mensagem)
        if (
            routed_context is ActiveContext.OBSIDIAN
            and not self._obsidian_available()
        ):
            self.context_router.state.active = (
                ActiveContext.NONE
                if previous_context is ActiveContext.OBSIDIAN
                else previous_context
            )
            return (
                "O Obsidian não está configurado nesta execução. "
                "Verifique /doctor ou use /config obsidian."
            )
        if (
            routed_context is ActiveContext.OBSIDIAN
            and self._requests_obsidian_write(mensagem)
            and not self._obsidian_write_available()
        ):
            return "A escrita no Obsidian está desabilitada ou indisponível."
        obsidian_response = self._obsidian_fast_response(mensagem)
        if obsidian_response is not None:
            return obsidian_response
        compound_response = self._compound_fast_response(mensagem)
        if compound_response is not None:
            return compound_response
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
        return self._process_normal_message(mensagem, response_mode)

    def _natural_doctor_response(
        self, message: str, *, response_mode: str = "text"
    ) -> str | None:
        normalized = self.context_router.normalize(message)
        if re.search(r"\b(?:doctor|doutor)\b", normalized) is None:
            return None
        if re.search(
            r"\b(?:rode|roda|rodar|execute|executa|executar|verifique|"
            r"verifica|cheque|checa|mostre|mostra|status|diagnostico)\b",
            normalized,
        ) or re.search(
            r"\b(?:teu|seu) (?:doctor|doutor)\b|"
            r"\bcomo (?:e que )?(?:ta|esta) (?:o )?(?:doctor|doutor)\b",
            normalized,
        ):
            return (
                self.doctor.voice_summary()
                if response_mode == "voice"
                else self.doctor.report()
            )
        return None

    def warm_up_llm(self) -> None:
        keep_alive = (
            self.config.ollama_keep_alive_game_mode
            if self.resource_manager.is_game_mode_active()
            else self.config.ollama_keep_alive_normal
        )
        self.llm.set_keep_alive(keep_alive)
        self.llm.warm_up()

    def _begin_tool_trace(self, request: str) -> None:
        self._current_tool_trace = []
        self._current_trace_request = request.strip()

    def _obsidian_fast_response(self, message: str) -> str | None:
        """Execute high-confidence Obsidian commands through real Tools.

        This prevents the LLM from narrating actions such as "anotado" or
        "abri" when no Tool actually ran. Ambiguous commands still fall back
        to the normal agent loop.
        """
        if self.context_router.state.active is not ActiveContext.OBSIDIAN:
            return None
        command = self.obsidian_command_parser.parse(message)
        if command is None:
            if self.obsidian_command_parser.looks_like_create_without_title(
                message
            ):
                return "Qual nome você quer dar à nota?"
            return None

        if command.action in {"create", "append"} and not self._obsidian_write_available():
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
            result = self._execute_tool(tool, arguments)
            if result.status != "success":
                self.memory.add_assistant_message(result.content)
                return result.content
            self._remember_operational_context(tool.name, arguments, result.content)
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

        # Abrir uma nota nomeada continua no agent loop para preservar o
        # roteamento contextual existente. Leituras nomeadas são SAFE e têm
        # fast path porque são o caso de voz que mais sofria com paráfrases.
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
            result = self._execute_tool(tool, arguments)
            if result.status != "success":
                self.memory.add_assistant_message(result.content)
                return result.content
            self._remember_operational_context(tool.name, arguments, result.content)
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
            return f'Não encontrei a nota "{command.reference or "anterior"}" no Obsidian.'
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
            last_result = self._execute_tool(tool, arguments)
            if last_result.status != "success":
                self.memory.add_assistant_message(last_result.content)
                return last_result.content
            self._remember_operational_context(
                tool.name, arguments, last_result.content
            )

        if command.action == "append":
            follow_up = self._execute_compound_follow_up(
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

    def _compound_fast_response(self, message: str) -> str | None:
        resolver = self.note_reference_resolver
        if resolver is None or not self._obsidian_available():
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
        opened = self._execute_tool(open_tool, arguments)
        if opened.status != "success":
            self.memory.add_assistant_message(opened.content)
            return opened.content
        self._remember_operational_context(
            open_tool.name, arguments, opened.content
        )
        read = self._execute_tool(read_tool, arguments)
        if read.status != "success":
            self.memory.add_assistant_message(read.content)
            return read.content
        self._remember_operational_context(
            read_tool.name, arguments, read.content
        )
        response = read.content.strip() or "A nota está vazia."
        self.memory.add_assistant_message(response)
        return response

    def _format_skills(self) -> str:
        try:
            skills = self.skill_store.list()
        except RuntimeError as error:
            return f"Não foi possível carregar as Skills: {error}"
        if not skills:
            return "Nenhuma Skill aprendida ainda."
        lines = ["Skills aprendidas:"]
        for skill in skills:
            lines.append(
                f"- {skill.id} | {skill.name} | "
                f"{skill.success_count}/{skill.execution_count} sucessos"
            )
        return "\n".join(lines)

    def _process_skill_command(self, command: str) -> str:
        if command.strip().casefold() == "/skill":
            return (
                "Uso: /skills | /skill learn <nome> | /skill show <id|nome> | "
                "/skill run <id|nome> | /skill forget <id|nome> | /skill sync"
            )
        learn = re.fullmatch(
            r'/skill\s+learn\s+"?(.+?)"?', command, flags=re.IGNORECASE
        )
        if learn is not None:
            return self._learn_last_skill(learn.group(1))
        show = re.fullmatch(
            r'/skill\s+show\s+"?(.+?)"?', command, flags=re.IGNORECASE
        )
        if show is not None:
            skill = self.skill_store.get(show.group(1))
            if skill is None:
                return "Skill não encontrada."
            lines = [
                f"Skill: {skill.name} ({skill.id})",
                f"Origem: {skill.original_request or 'não registrada'}",
                "Etapas:",
            ]
            lines.extend(
                f"{index}. {step.tool_name} {step.arguments}"
                for index, step in enumerate(skill.steps, 1)
            )
            return "\n".join(lines)
        run = re.fullmatch(
            r'/skill\s+run\s+"?(.+?)"?', command, flags=re.IGNORECASE
        )
        if run is not None:
            return self._run_skill(run.group(1))
        forget = re.fullmatch(
            r'/skill\s+forget\s+"?(.+?)"?', command, flags=re.IGNORECASE
        )
        if forget is not None:
            skill = self.skill_store.delete(forget.group(1))
            if skill is None:
                return "Skill não encontrada."
            self._mirror_skill(skill, active=False)
            return f"Skill '{skill.name}' removida do registro executável."
        if re.fullmatch(r"/skill\s+sync", command, flags=re.IGNORECASE):
            if self.skill_mirror is None:
                return "Espelho de Skills no Obsidian não está disponível."
            count = 0
            for skill in self.skill_store.list():
                self._mirror_skill(skill)
                count += 1
            return f"{count} Skill(s) sincronizada(s) com o Obsidian."
        return "Comando de Skill inválido. Use /skill para ver as opções."

    def _learned_skill_response(self, message: str) -> str | None:
        try:
            skill = self.skill_matcher.match(message, self.skill_store.list())
        except RuntimeError:
            return None
        if skill is None:
            return None
        return self._run_skill(skill.id)

    def _natural_skill_run_name(self, message: str) -> str | None:
        # Speech recognizers commonly hear the English word "skill" as
        # "esquivo/esquilo" in pt-BR. These variants are accepted only after
        # an explicit execution verb, so normal dictated text is untouched.
        match = re.search(
            r"(?:^|[.!?]\s*|\bent[aã]o[ ,]+|\bagora[ ,]+)"
            r"(?:jarvis[ ,]+)?(?:execute|executa|rode|roda|rodar|faca|faça)\s+"
            r"(?:(?:a|essa|esta|esse)\s+)?"
            r"(?:skill|esquivo|esquilo|esquil|rotina|procedimento)"
            r"(?:\s+a[ií])?\s*[,.:;-]?\s*(.+)$",
            message.strip(),
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        name = match.group(1).strip(" .\"'")
        return re.sub(r"\.md$", "", name, flags=re.IGNORECASE).strip()

    def _run_skill(self, identifier: str) -> str:
        try:
            skill = self.skill_store.get(identifier)
            if skill is None:
                skill = self.skill_matcher.match_explicit_name(
                    identifier, self.skill_store.list()
                )
        except RuntimeError as error:
            return f"Não foi possível carregar as Skills: {error}"
        if skill is None:
            return "Skill não encontrada."
        result = self.skill_executor.execute(skill)
        for step, execution in zip(skill.steps, result.results):
            if execution.status == "success":
                self._remember_operational_context(
                    step.tool_name, step.arguments, execution.content
                )
        skill.mark_execution(result.success)
        self.skill_store.save(skill)
        self._mirror_skill(skill)
        if not result.success:
            return result.message
        if result.results and skill.steps[-1].tool_name in {
            "obsidian_read_note", "read_file", "get_datetime",
            "get_runtime_info",
        }:
            return result.results[-1].content
        return "Pronto."

    @staticmethod
    def _natural_skill_list_requested(message: str) -> bool:
        normalized = SkillMatcher.normalize(message)
        has_skill = re.search(
            r"\b(?:skill|skills|rotina|rotinas|procedimento|procedimentos)\b",
            normalized,
        ) is not None
        asks_inventory = re.search(
            r"\b(?:quais|liste|lista|listar|mostre|mostra|tem|tenho)\b",
            normalized,
        ) is not None
        return has_skill and asks_inventory

    def _natural_skill_list_response(self) -> str:
        try:
            skills = self.skill_store.list()
        except RuntimeError as error:
            return f"Não foi possível carregar as Skills: {error}"
        if not skills:
            return "Ainda não tenho nenhuma Skill aprendida."
        names = ", ".join(skill.name for skill in skills)
        return f"Tenho estas Skills: {names}."

    def _natural_skill_learning_name(self, message: str) -> str | None:
        value = message.strip()
        patterns = (
            re.compile(
                r"(?:(?:agora|entao|beleza|boa)[ ,]+)*"
                r"(?:jarvis[ ,]+)?(?:aprenda|aprende|memorize|salve)\s+"
                r"(?:isso|esse procedimento|essa rotina|esse processo|"
                r"essa sequ[eê]ncia)\s+(?:(?:de\s+)?como|de)\s+(.+)$",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:(?:agora|entao|beleza|boa|pronto)[ ,!]+)*"
                r"(?:jarvis[ ,]+)?(?:aprenda|aprende|memorize|salve)\s+"
                r"(?:(?:esse|essa|esses|essas|o|a)\s+)?"
                r"(?:procedimento|procedimentos|rotina|rotinas|processo|processos|"
                r"sequ[eê]ncia|skill)\b.*?\s+como\s+(.+)$",
                re.IGNORECASE,
            ),
        )
        for pattern in patterns:
            match = pattern.fullmatch(value)
            if match is not None:
                return match.group(1).strip(" .\"'")
        return None

    @staticmethod
    def _skill_control_fallback(message: str) -> str | None:
        normalized = SkillMatcher.normalize(message)
        learning = bool(
            re.search(r"\b(?:aprenda|aprende|memorize|salve)\b", normalized)
            and re.search(
                r"\b(?:procedimento|rotina|processo|sequencia|skill)\b",
                normalized,
            )
        )
        if learning:
            return (
                "Entendi que você quer salvar um procedimento, mas não consegui "
                "identificar o nome. Diga: aprenda esse procedimento como "
                "<nome da Skill>."
            )
        running = bool(
            re.search(
                r"\b(?:execute|executa|rode|roda|rodar|faca)\b", normalized
            )
            and re.search(
                r"\b(?:skill|esquivo|esquilo|esquil|rotina|procedimento)\b",
                normalized,
            )
        )
        if running:
            return (
                "Entendi que você quer executar uma Skill, mas não consegui "
                "identificar qual. Diga: executa a Skill <nome>."
            )
        return None

    def _learn_last_skill(self, name: str) -> str:
        try:
            existing = self.skill_store.get(name)
            if existing is not None:
                return (
                    f"Já existe uma Skill chamada '{existing.name}'. "
                    "Remova-a primeiro se quiser reaprender."
                )
            skill = self.skill_learner.from_trace(
                name, self.last_tool_trace_request, self.last_tool_trace
            )
            self.skill_store.save(skill)
            mirror_path = self._mirror_skill(skill)
        except (RuntimeError, ValueError) as error:
            return str(error)
        mirror = f" Espelho criado em {mirror_path}." if mirror_path else ""
        return (
            f"Skill '{skill.name}' aprendida com {len(skill.steps)} etapa(s)."
            f"{mirror}"
        )

    def _mirror_skill(
        self, skill: Any, *, active: bool = True
    ) -> str | None:
        mirror = self.skill_mirror
        if mirror is None:
            return None
        try:
            return mirror.sync(skill, active=active)
        except (OSError, PermissionError, ValueError):
            return None

    def _obsidian_available(self) -> bool:
        vault = self.config.obsidian_vault_path
        return bool(
            self.config.obsidian_enabled
            and vault is not None
            and vault.is_dir()
            and self.config.obsidian_read_enabled
            and self.tool_registry.get("obsidian_search_notes") is not None
            and self.tool_registry.get("obsidian_read_note") is not None
        )

    def _obsidian_write_available(self) -> bool:
        return bool(
            self.config.obsidian_write_enabled
            and self.tool_registry.get("obsidian_append_note") is not None
        )

    def _requests_obsidian_write(self, message: str) -> bool:
        normalized = self.context_router.normalize(message)
        return re.search(
            r"\b(?:adicione|adiciona|adicionar|edite|edita|editar|"
            r"coloque|coloca|colocar|inserir|insira|acrescente|"
            r"anote|anota|anotar|escreva|escrever|registre|registrar)\b",
            normalized,
        ) is not None

    def _process_config_command(self, command: str) -> str:
        normalized = command.strip()
        if normalized in {"/config", "/config obsidian"}:
            vault = self.config.obsidian_vault_path
            status = "OK" if self._obsidian_available() else "NOT CONFIGURED"
            vault_name = vault.name if vault is not None else "NOT CONFIGURED"
            write = "ENABLED" if self.config.obsidian_write_enabled else "DISABLED"
            error = (
                f"\nLocal settings: INVALID ({self.config.settings_error})"
                if self.config.settings_error else ""
            )
            return (
                f"Obsidian: {status}\nVault: {vault_name}\nWrite: {write}"
                f"{error}"
            )

        vault_match = re.fullmatch(
            r'/config\s+obsidian\s+vault\s+"?(.+?)"?',
            normalized,
            flags=re.IGNORECASE,
        )
        if vault_match is not None:
            vault = Path(vault_match.group(1)).expanduser()
            if not vault.is_dir():
                return "Diretório do Vault não encontrado. Configuração mantida."
            try:
                self.config.save_obsidian_settings(vault=vault)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"Vault '{vault.name}' salvo. Reinicie o Jarvis para "
                "registrar as Tools do Obsidian."
            )

        write_match = re.fullmatch(
            r"/config\s+obsidian\s+write\s+(on|off)",
            normalized,
            flags=re.IGNORECASE,
        )
        if write_match is not None:
            enabled = write_match.group(1).casefold() == "on"
            try:
                self.config.save_obsidian_settings(write=enabled)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            state = "habilitada" if enabled else "desabilitada"
            return f"Escrita no Obsidian {state} na configuração local."

        if normalized.casefold() == "/config voice":
            reference = self.config.tts_reference_audio_path
            reference_label = str(reference) if reference is not None else "nenhuma"
            piper_ready = (
                self.config.tts_piper_model_path.is_file()
                and self.config.tts_piper_config_path.is_file()
            )
            return (
                f"TTS: {self.config.tts_provider}\n"
                f"Dispositivo neural: {self.config.tts_device}\n"
                f"Idioma: {self.config.tts_language_id}\n"
                f"Voz rápida Piper: {'pronta' if piper_ready else 'não preparada'}\n"
                f"Ritmo Piper: {self.config.tts_piper_pace} "
                f"(length scale {self.config.tts_piper_length_scale:.2f})\n"
                f"Voz de referência: {reference_label}"
            )

        mode_match = re.fullmatch(
            r"/config\s+voice\s+mode\s+(fast|quality|auto)",
            normalized,
            flags=re.IGNORECASE,
        )
        if mode_match is not None:
            mode = mode_match.group(1).casefold()
            provider = {
                "fast": "piper",
                "quality": "chatterbox",
                "auto": "auto",
            }[mode]
            try:
                self.config.save_voice_settings(provider=provider)
            except (OSError, ValueError) as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"Modo de voz '{mode}' salvo ({provider}). "
                "Reinicie o Jarvis para aplicar."
            )

        provider_match = re.fullmatch(
            r"/config\s+voice\s+provider\s+(auto|sapi|piper|chatterbox)",
            normalized,
            flags=re.IGNORECASE,
        )
        if provider_match is not None:
            provider = provider_match.group(1).casefold()
            try:
                self.config.save_voice_settings(provider=provider)
            except (OSError, ValueError) as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"TTS '{provider}' salvo. Reinicie o Jarvis para aplicar."
            )

        pace_match = re.fullmatch(
            r"/config\s+voice\s+pace\s+(fast|natural|calm)",
            normalized,
            flags=re.IGNORECASE,
        )
        if pace_match is not None:
            pace = pace_match.group(1).casefold()
            try:
                self.config.save_voice_settings(piper_pace=pace)
            except (OSError, ValueError) as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"Ritmo Piper alterado para '{pace}'. "
                "Reinicie o Jarvis para aplicar."
            )

        reference_off = re.fullmatch(
            r"/config\s+voice\s+reference\s+off",
            normalized,
            flags=re.IGNORECASE,
        )
        if reference_off is not None:
            try:
                self.config.save_voice_settings(clear_reference=True)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            return "Voz de referência removida. Reinicie o Jarvis para aplicar."

        reference_match = re.fullmatch(
            r'/config\s+voice\s+reference\s+"?(.+?)"?',
            normalized,
            flags=re.IGNORECASE,
        )
        if reference_match is not None:
            reference = Path(reference_match.group(1)).expanduser()
            if not reference.is_file():
                return "Áudio de referência não encontrado. Configuração mantida."
            try:
                self.config.save_voice_settings(reference_audio=reference)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"Voz de referência '{reference.name}' salva. Reinicie o Jarvis "
                "para aplicar."
            )

        if normalized.casefold() == "/config persona":
            return (
                "Tratamento: " + self.config.assistant_address_mode + "\n"
                "Estilo: " + self.config.assistant_response_style
            )

        treatment_match = re.fullmatch(
            r"/config\s+persona\s+treatment\s+(senhor|natural|direto)",
            normalized,
            flags=re.IGNORECASE,
        )
        if treatment_match is not None:
            treatment = treatment_match.group(1).casefold()
            try:
                self.config.save_persona_settings(address_mode=treatment)
            except (OSError, ValueError) as error:
                return f"Não foi possível salvar a configuração: {error}."
            return f"Modo de tratamento alterado para '{treatment}'."

        return (
            "Uso: /config | /config obsidian | /config voice | /config persona | "
            '/config obsidian vault "C:\\caminho" | '
            "/config obsidian write on|off | "
            "/config voice provider auto|sapi|piper|chatterbox | "
            "/config voice mode fast|quality|auto | "
            "/config voice pace fast|natural|calm | "
            '/config voice reference "C:\\voz.wav"|off | '
            "/config persona treatment senhor|natural|direto"
        )

    def _process_normal_message(
        self, mensagem: str, response_mode: str = "text"
    ) -> str:

        active_context = self.context_router.state.active
        available_tools = self.tool_selection_router.select(
            mensagem,
            active_context,
            self.tool_registry.list_tools(),
        )
        available_tools.sort(
            key=lambda tool: not self._tool_matches_context(
                tool.name, active_context
            )
        )
        tools = [tool_to_ollama_schema(tool) for tool in available_tools]

        self.memory.add_user_message(mensagem)
        messages: list[dict[str, Any]] = self._system_context(response_mode)
        messages.extend(self.memory.get_history())
        successful_tool_names: set[str] = set()
        tool_retry_used = False

        for _ in range(self.config.max_tool_iterations):
            llm_started = time.perf_counter()
            try:
                response = self.llm.generate(
                    messages=messages,
                    tools=tools,
                )
            finally:
                self.last_latency["llm_seconds"] += (
                    time.perf_counter() - llm_started
                )
                self.last_latency["llm_calls"] += 1
            for tool_call in response.tool_calls:
                self._route_tool_call(mensagem, tool_call)
            requested_tool_names = {
                tool_call.name for tool_call in response.tool_calls
            }
            if response.tool_calls:
                # Conteúdo textual adjacente a uma tool call é protocolo
                # interno e nunca é candidato a resposta final.
                response.content = ""
            messages.append(response.to_message())

            if not response.tool_calls:
                action_requested = self.tool_selection_router.looks_like_action(
                    mensagem, self.context_router.state.active
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
                    final_response = self._finalize_llm_response(
                        response.content,
                        messages,
                        tool_executed=bool(successful_tool_names),
                    )
                self.memory.add_assistant_message(final_response)
                return final_response

            for tool_call in response.tool_calls:
                tool = self.tool_registry.get(tool_call.name)
                resolved_arguments = dict(tool_call.arguments)
                execution_result: ToolExecutionResult | None = None

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
                        if execution_result.status == "success":
                            successful_tool_names.add(tool.name)
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
                if (
                    tool is not None
                    and execution_result is not None
                    and execution_result.status == "success"
                ):
                    follow_up = self._execute_compound_follow_up(
                        mensagem,
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
                                    ToolCallRequest(
                                        follow_name, follow_arguments
                                    )
                                ],
                            ).to_message()
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_name": follow_name,
                                "content": follow_result.content,
                            }
                        )
                        requested_tool_names.add(follow_name)

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
        tool_started = time.perf_counter()
        try:
            execute_with_grant = getattr(
                self.tool_executor, "execute_with_grant", None
            )
            if (
                grant is not None
                and interaction_id is not None
                and callable(execute_with_grant)
            ):
                result = execute_with_grant(
                    tool, grant, interaction_id, **arguments
                )
            else:
                execute_with_result = getattr(
                    self.tool_executor,
                    "execute_with_result",
                    None,
                )
                if callable(execute_with_result):
                    result = execute_with_result(tool, **arguments)
                else:
                    content = self.tool_executor.execute(tool, **arguments)
                    result = ToolExecutionResult(str(content), "success", True)
            if result.status == "success":
                self._current_tool_trace.append(
                    ToolTraceStep(tool.name, dict(arguments))
                )
            if self.config.latency_debug_enabled:
                safe_arguments = {
                    key: (
                        "<redacted>"
                        if key in {"content", "text"}
                        else value
                    )
                    for key, value in arguments.items()
                }
                print(
                    f"[tool] {tool.name} {safe_arguments} -> {result.status}"
                )
            return result
        finally:
            self.last_latency["tool_seconds"] += (
                time.perf_counter() - tool_started
            )
            self.last_latency["tool_calls"] += 1

    def _execute_compound_follow_up(
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
        valid, _error = self.argument_validator.validate(
            tool, planned.arguments
        )
        if not valid:
            return None
        intent = self.intent_guard.validate(
            message, tool, planned.arguments
        )
        if not intent.allowed:
            return None
        result = self._execute_tool(tool, planned.arguments)
        if result.status == "success":
            self._remember_operational_context(
                tool.name, planned.arguments, result.content
            )
        return tool.name, dict(planned.arguments), result

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

    def _system_context(
        self, response_mode: str = "text"
    ) -> list[dict[str, Any]]:
        memories = self.memory_store.get_all()
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
        if active is ActiveContext.OBSIDIAN:
            lines.append("Chame a entidade ativa de nota, não de arquivo.")
            if self._obsidian_write_available():
                lines.append(
                    "A escrita no Obsidian está habilitada. Para pedidos "
                    "explícitos de adicionar, anotar, escrever ou acrescentar "
                    "conteúdo em uma nota, use obsidian_append_note; para uma "
                    "nota nova, use obsidian_create_note. Não diga que não pode "
                    "escrever no Obsidian quando essas Tools estiverem disponíveis."
                )

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
        *,
        tool_executed: bool = False,
    ) -> str:
        validated = self.final_response_firewall.validate(content)
        if validated.accepted:
            grounded = self.execution_claim_guard.validate(
                validated.text,
                tool_executed=tool_executed,
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

        # O retry recebe apenas o histórico e resultados já produzidos.
        # Não há schema de Tool, executor ou grant neste caminho.
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
            self.last_latency["llm_seconds"] += (
                time.perf_counter() - llm_started
            )
            self.last_latency["llm_calls"] += 1
        if retry.tool_calls:
            return self.SAFE_FALLBACK_RESPONSE
        retried = self.final_response_firewall.validate(retry.content)
        if not retried.accepted:
            return "Não consegui formular uma resposta segura. Tente novamente."
        grounded = self.execution_claim_guard.validate(
            retried.text,
            tool_executed=tool_executed,
        )
        if not grounded.accepted:
            return (
                "Não executei nenhuma Tool para sustentar esse resultado. "
                "Posso tentar a ação novamente de forma segura."
            )
        return retried.text

    def _fast_path_response(self, message: str) -> str | None:
        match = self.fast_path_router.match(message)
        if match is None:
            return None
        tool = self.tool_registry.get("get_datetime")
        if tool is None:
            return None
        self.memory.add_user_message(message)
        result = self._execute_tool(tool, {})
        if result.status != "success":
            self.memory.add_assistant_message(result.content)
            return result.content
        response = format_datetime_tool_result(result.content, match.intent)
        self.memory.add_assistant_message(response)
        return response

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
