from typing import Any
import json
import re
import shlex
import threading
import time

from jarvis.security.authorization import AuthorizationGrant
from jarvis.ui.control_center_model import ControlCenterBus
from jarvis.vision.service import VisionService

from jarvis.application.agent_loop_coordinator import AgentLoopCoordinator
from jarvis.application.control_center_coordinator import ControlCenterCoordinator
from jarvis.application.config_command_coordinator import ConfigCommandCoordinator
from jarvis.application.intent_coordinator import IntentCoordinator
from jarvis.application.obsidian_coordinator import ObsidianCoordinator
from jarvis.application.response_coordinator import ResponseCoordinator
from jarvis.application.semantic_read_dispatcher import SemanticReadDispatcher
from jarvis.application.local_control_dispatcher import LocalControlDispatcher
from jarvis.application.safe_browser_dispatcher import SafeBrowserDispatcher
from jarvis.application.skill_coordinator import SkillCoordinator
from jarvis.application.tool_execution_coordinator import ToolExecutionCoordinator
from jarvis.application.turn_policy import TurnKind, TurnPolicy
from jarvis.application.telemetry import TelemetryBus

from jarvis.config import JarvisConfig
from jarvis.context.recent import RecentContext
from jarvis.context.active import ActiveContext, ContextRouter
from jarvis.context.compound_actions import CompoundActionPlanner
from jarvis.context.pending_action import (
    PendingAction,
    PendingActionContext,
    PendingActionState,
)
from jarvis.context.note_reference_resolver import NoteReferenceResolver
from jarvis.context.file_reference_resolver import FileReferenceResolver
from jarvis.doctor import JarvisDoctor
from jarvis.llm.base import LLMClient
from jarvis.llm.firewall import FinalResponseFirewall
from jarvis.llm.grounding import ExecutionClaimGuard
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.memory.store import MemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.cancellation import CancellationInterpreter
from jarvis.runtime.fast_path import (
    FastPathRouter,
    FastIntent,
    format_datetime_tool_result,
)
from jarvis.runtime.tool_selection import ToolSelectionRouter
from jarvis.runtime.obsidian_commands import ObsidianCommandParser
from jarvis.security.risk import RiskLevel
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.confirmation import security_prompt
from jarvis.security.confirmation_intent import ConfirmationInterpreter
from jarvis.security.confirmation_request import ConfirmationPurpose
from jarvis.security.confirmation_session import (
    ConfirmationDecision,
    ConfirmationEventKind,
    ConfirmationSession,
    ConfirmationSessionManager,
    ConfirmationSessionState,
    digest_arguments,
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
        telemetry_bus: TelemetryBus | None = None,
        control_center_bus: ControlCenterBus | None = None,
        vision_service: VisionService | None = None,
        application_control=None,
        organization=None,
        accounts=None,
    ) -> None:
        self.llm = llm
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.organization = organization
        self.application_control = application_control
        self.application_dispatcher = None
        if application_control is not None:
            from jarvis.application.application_dispatcher import ApplicationDispatcher
            self.application_dispatcher = ApplicationDispatcher(tool_registry, self._execute_tool, application_control)
        self.memory = memory
        self.telemetry_bus = telemetry_bus
        self.memory_store = (
            memory_store
            if memory_store is not None
            else InMemoryMemoryStore()
        )
        self.memory_store.cleanup_expired()
        permissions = getattr(self.tool_executor, "permissions", None)
        if permissions is not None:
            permissions.on_change = self._permissions_changed
            from jarvis.application.administration import AdministrationService
            self.administration = AdministrationService(permissions, self.memory_store,
                self.tool_registry, config or JarvisConfig(), revoke=self._permissions_changed,
                audit=self.tool_executor.audit_logger, applications=application_control,
                organization=organization,
                accounts=accounts,
                execute_tool=self._execute_tool)
            self.administration.capability_policy = self.tool_executor.capability_policy
            self.memory_store.on_mutation = self.memory.clear
            from jarvis.memory.forget_tool import MemoryForgetTool
            self._memory_forget_tool = MemoryForgetTool(self.memory_store)
        self.config = config or JarvisConfig()
        from jarvis.application.daily_briefing import DailyBriefing, LocalBriefingMemoryTool
        self.daily_briefing = DailyBriefing(self.config)
        self._briefing_memory_tool = LocalBriefingMemoryTool(
            self.memory_store, self.config.briefing_timezone)
        self.last_briefing_text = ''
        from jarvis.application.routines import RoutineService
        self.routines = RoutineService(self)
        from jarvis.application.delegation import DelegationService
        self.delegation = DelegationService(self)
        if hasattr(self, 'administration'):
            self.administration.routines = self.routines
            self.administration.delegation = self.delegation
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
        self.confirmation_sessions = ConfirmationSessionManager(
            first_reminder_seconds=(
                self.config.confirmation_first_reminder_seconds
            ),
            reminder_interval_seconds=(
                self.config.confirmation_reminder_interval_seconds
            ),
            max_reminders=self.config.confirmation_max_reminders,
            timeout_seconds=self.config.confirmation_session_timeout_seconds,
            cleanup=self._cleanup_confirmation_session,
        )
        self.cancellation_interpreter = CancellationInterpreter()
        self.confirmation_interpreter = ConfirmationInterpreter()
        self.note_reference_resolver = note_reference_resolver
        self.context_router = context_router or ContextRouter()
        self.fast_path_router = FastPathRouter()
        self.voice_diagnostic = None
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
        self.turn_policy = TurnPolicy()
        if hasattr(self.tool_executor, "confirmation_requester"):
            self.tool_executor.confirmation_requester = (
                self._request_critical_confirmation
            )
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
        self.tool_execution_coordinator = ToolExecutionCoordinator(
            self.tool_registry,
            self.tool_executor,
            self.argument_validator,
            self.intent_guard,
            self.compound_action_planner,
            self.file_reference_resolver,
            self.note_reference_resolver,
            self.target_confirmation,
            self.pending_actions,
            self.cancellation_interpreter,
            self.confirmation_interpreter,
            self.context_router,
            self.recent_context,
            self._execute_tool,
        )
        self.skill_coordinator = SkillCoordinator(
            self.skill_store,
            self.skill_learner,
            self.skill_matcher,
            self.skill_executor,
            self.skill_mirror,
            self._remember_operational_context,
        )
        self.obsidian_coordinator = ObsidianCoordinator(
            self.config,
            self.tool_registry,
            self.argument_validator,
            self.intent_guard,
            self.memory,
            self.note_reference_resolver,
            self.context_router,
            self.obsidian_command_parser,
            self.compound_action_planner,
            self._execute_tool,
            self._execute_compound_follow_up,
            self._remember_operational_context,
        )
        self.config_command_coordinator = ConfigCommandCoordinator(
            self.config, self._obsidian_available
        )
        self.response_coordinator = ResponseCoordinator(
            self.config,
            self.context_router,
            self.recent_context,
            self.memory_store,
            self.tool_registry,
            self.llm,
            self.final_response_firewall,
            self.execution_claim_guard,
            self.confirmation_interpreter,
            self._obsidian_write_available,
            lambda: self.last_latency,
            self.SAFE_FALLBACK_RESPONSE,
        )
        self.agent_loop_coordinator = AgentLoopCoordinator(
            self.config,
            self.llm,
            self.memory,
            self.context_router,
            self.tool_registry,
            self.tool_selection_router,
            self.argument_validator,
            self.intent_guard,
            self.pending_actions,
            self.tool_execution_coordinator,
            self.response_coordinator,
            self._generate_llm_response,
            self._execute_tool,
        )
        self.intent_coordinator = IntentCoordinator(
            self.config,
            self.resource_manager,
            self.llm,
            self.context_router,
            self.doctor,
            self.skill_coordinator,
            self.obsidian_coordinator,
            self.tool_execution_coordinator,
            self.response_coordinator,
            self.turn_policy,
            self._fast_path_response,
            self._runtime_info_response,
            self.agent_loop_coordinator.process,
            lambda: (self.last_tool_trace_request, self.last_tool_trace),
            semantic_read_dispatcher=(
                SemanticReadDispatcher(
                    self.tool_registry,
                    self._execute_tool,
                )
                if self.config.semantic_read_tools_enabled else None
            ),
            local_control_dispatcher=(
                LocalControlDispatcher(
                    self.tool_registry,
                    self._execute_tool,
                )
                if (self.config.local_control_enabled or self.config.gsmtc_enabled)
                else None
            ),
            safe_browser_dispatcher=(
                SafeBrowserDispatcher(self.tool_registry, self._execute_tool)
                if self.config.safe_browser_enabled else None
            ),
            application_dispatcher=self.application_dispatcher,
        )
        self.control_center_bus = control_center_bus or ControlCenterBus()
        self.control_center_coordinator = ControlCenterCoordinator(
            self.memory_store,
            self.skill_store,
            vision_service,
            self.telemetry_bus or TelemetryBus(),
            self.control_center_bus,
        )
        self._latency_turn_started_at: float | None = None
        self._turn_lock = threading.RLock()

    def set_voice_diagnostic(self, recorder: Any) -> None:
        self.voice_diagnostic = recorder

    def _voice_trace(self, event: str, **fields: object) -> None:
        recorder = self.voice_diagnostic
        if recorder is not None:
            recorder.record(event, **fields)

    def run(self) -> None:
        print("Jarvis iniciado.")
        print("Digite 'sair' para encerrar.\n")

        try:
            while True:
                confirmation_context = self.current_confirmation_context()
                mensagem = input("Você: ")

                if mensagem.lower() == "sair":
                    print("Jarvis: Até mais.")
                    break

                resposta = self.processar_mensagem(
                    mensagem,
                    confirmation_session_id=(
                        confirmation_context[0]
                        if confirmation_context is not None
                        else None
                    ),
                    confirmation_challenge_id=(
                        confirmation_context[1]
                        if confirmation_context is not None
                        else None
                    ),
                )
                print(f"Jarvis: {resposta}")
        finally:
            self.close_confirmation_sessions()

    def processar_mensagem(
        self,
        mensagem: str,
        manage_runtime: bool = True,
        response_mode: str = "text",
        confirmation_session_id: str | None = None,
        confirmation_challenge_id: str | None = None,
    ) -> str:
        with self._turn_lock:
            self._work_cancelled = threading.Event()
            permissions = getattr(getattr(self, "tool_executor", None), "permissions", None)
            if permissions is not None:
                permissions.read()
            try:
                return self._processar_mensagem_serialized(
                    mensagem,
                    manage_runtime=manage_runtime,
                    response_mode=response_mode,
                    confirmation_session_id=confirmation_session_id,
                    confirmation_challenge_id=confirmation_challenge_id,
                )
            except PermissionError:
                return "Operação bloqueada pela política local, memória alterada ou sessão temporária. Revise a Central de Permissões e a Memória."

    def _processar_mensagem_serialized(
        self,
        mensagem: str,
        manage_runtime: bool = True,
        response_mode: str = "text",
        confirmation_session_id: str | None = None,
        confirmation_challenge_id: str | None = None,
    ) -> str:
        stripped_message = mensagem.strip()
        command = stripped_message.lower()
        delegation_response = self.delegation.dispatch(stripped_message)
        if delegation_response is not None:
            self.last_response_private_calendar = True
            return delegation_response
        routine_response = self.routines.dispatch(stripped_message)
        if routine_response is not None:
            self.last_briefing_text = ''
            self.last_response_private_calendar = True
            return routine_response
        if hasattr(self, "administration") and command.rstrip("?") in {
            "/permissions", "o que o jarvis pode acessar", "o que você pode acessar", "o que voce pode acessar",
        }:
            inventory = self.administration.inventory()
            if not inventory["valid"]:
                return "Configuração de permissões inválida: operações gerenciadas bloqueadas. Revise a Central de Permissões."
            return "O que o JARVIS pode acessar?\n" + "\n".join(
                f'{row["group"]}: {row["title"]} — {row["effective"]}' for row in inventory["rows"]
            ) + "\nCredencial não concede autorização. Revogar não desfaz efeitos já realizados."
        from jarvis.integrations.google.drive_docs.routing import looks_private_document
        private_tool = self.tool_registry.get('google_drive_find_files')
        if private_tool is not None and not looks_private_document(mensagem):
            private_tool.service.clear()

        measuring_confirmation = self.has_pending_confirmation()
        if measuring_confirmation:
            self._start_latency_turn()
        try:
            confirmation_response = self._process_confirmation_message(
                stripped_message,
                confirmation_session_id,
                confirmation_challenge_id,
            )
        except Exception:
            if measuring_confirmation:
                self._finish_latency_turn()
            raise
        if confirmation_response is not None:
            if measuring_confirmation:
                self._finish_latency_turn()
            return confirmation_response

        from jarvis.application.organization_routing import dispatch as organization_dispatch
        organization_response = organization_dispatch(self, stripped_message)
        if organization_response is not None:
            return organization_response

        if command.startswith('/app') and self.application_dispatcher is not None:
            decision = self.application_dispatcher.dispatch(stripped_message)
            return decision.response or 'Pedido de catálogo inválido; nenhuma operação executada.'

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
                return (
                    "Uso: /remember <texto> [--categoria <cat>] "
                    "[--sensibilidade <public|private|sensitive>] "
                    "[--retencao <dias>] [--metadados <json>]"
                )

            try:
                args = shlex.split(content)
            except ValueError:
                return "Argumentos de memória inválidos. Verifique as aspas."
            category = "user"
            sensitivity = "private"
            retention_days: int | None = None
            metadata: dict[str, Any] = {}
            content_parts: list[str] = []

            i = 0
            while i < len(args):
                arg = args[i]
                if arg in {
                    "--categoria",
                    "--sensibilidade",
                    "--retencao",
                    "--metadados",
                } and i + 1 >= len(args):
                    return f"A opção {arg} exige um valor."
                if arg == "--categoria":
                    category = args[i + 1]
                    i += 2
                elif arg == "--sensibilidade":
                    sensitivity = args[i + 1]
                    i += 2
                elif arg == "--retencao":
                    try:
                        retention_days = int(args[i + 1])
                    except ValueError:
                        return "Retenção deve ser um número inteiro de dias."
                    if retention_days <= 0:
                        return "Retenção deve ser maior que zero."
                    i += 2
                elif arg == "--metadados":
                    try:
                        metadata = json.loads(args[i + 1])
                    except (json.JSONDecodeError, TypeError):
                        return "Metadados devem ser JSON válido."
                    if not isinstance(metadata, dict):
                        return "Metadados devem ser um objeto JSON."
                    i += 2
                elif arg.startswith("--"):
                    return f"Opção de memória desconhecida: {arg}."
                else:
                    content_parts.append(arg)
                    i += 1

            actual_content = " ".join(content_parts).strip()
            if not actual_content:
                return "Uso: /remember <texto> [opções]"

            try:
                entry = self.memory_store.add(
                    actual_content,
                    category=category,
                    sensitivity=sensitivity,
                    retention_days=retention_days,
                    metadata=metadata,
                )
            except ValueError as error:
                return f"Memória inválida: {error}"
            return (
                f"Memória armazenada com ID: {entry.id}. "
                f"Sensibilidade: {entry.sensitivity.value}. "
                f"Retenção: {entry.retention_days or 'indefinida'} dias."
            )

        if command == "/memories":
            memories = self.memory_store.get_all()
            if not memories:
                return "Nenhuma memória persistente armazenada."

            lines = ["Memórias persistentes:"]
            lines.extend(
                f"- {entry.id} | {entry.category} | {entry.sensitivity.value} | "
                f"ret={entry.retention_days or 'indefinida'} | {entry.content}"
                for entry in memories
            )
            return "\n".join(lines)

        if command == "/consent" or command.startswith("/consent "):
            args = stripped_message[len("/consent"):].strip().split()
            if not args or args[0] not in {"grant", "revoke", "list"}:
                return "Uso: /consent grant|revoke|list <id>"

            action = args[0]
            if action == "list":
                memories = self.memory_store.get_all()
                if not memories:
                    return "Nenhuma memória persistente armazenada."
                return "\n".join(
                    f"{'SIM' if m.consent_given else 'NAO'} "
                    f"{m.id} | {m.content[:50]}"
                    for m in memories
                )

            if len(args) < 2:
                return "Uso: /consent grant|revoke <id>"

            memory_id = args[1]
            memories = self.memory_store.get_all()
            target = next((m for m in memories if m.id == memory_id), None)
            if target is None:
                return "Memória não encontrada."

            if action == "grant":
                if not self.memory_store.update_consent(memory_id, True):
                    return "Memória não encontrada."
                return "Consentimento concedido."
            else:
                if not self.memory_store.update_consent(memory_id, False):
                    return "Memória não encontrada."
                return "Consentimento revogado."

        if command == "/forget" or command.startswith("/forget "):
            memory_id = stripped_message[len("/forget"):].strip()
            if not memory_id:
                return "Uso: /forget <id>"

            if hasattr(self, "_memory_forget_tool"):
                return self._execute_tool(self._memory_forget_tool, {"id": memory_id}).content

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
        telemetry_bus = self.telemetry_bus
        if started is not None and telemetry_bus is not None:
            try:
                telemetry_bus.publish_turn(
                    turn_ms=round(
                        float(self.last_latency["harness_seconds"]) * 1000
                    ),
                    llm_ms=round(float(self.last_latency["llm_seconds"]) * 1000),
                    llm_calls=int(self.last_latency["llm_calls"]),
                    tool_ms=round(float(self.last_latency["tool_seconds"]) * 1000),
                    tool_calls=int(self.last_latency["tool_calls"]),
                )
            except Exception:
                # Telemetria visual nunca pode alterar a resposta do Core.
                pass
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
        return (
            self.pending_actions.has_pending()
            or self.confirmation_sessions.current() is not None
        )

    def has_pending_confirmation(self) -> bool:
        return self.confirmation_sessions.current() is not None

    def current_confirmation_context(self) -> tuple[str, str] | None:
        permissions = getattr(self.tool_executor, "permissions", None)
        if permissions is not None:
            permissions.read()
        return self.confirmation_sessions.context()

    def _permissions_changed(self) -> None:
        self.tool_executor.safety_manager.invalidate_authorizations()
        if hasattr(self, "confirmation_sessions"):
            self.cancel_pending_confirmation()
        self.memory.clear()
        if hasattr(self.memory_store, "invalidate_context"):
            self.memory_store.invalidate_context()

    def confirmation_state(self) -> str | None:
        state = self.confirmation_sessions.state()
        return state.value if state is not None else None

    def pop_confirmation_event(self) -> tuple[str, int, str] | None:
        event = self.confirmation_sessions.pop_event()
        if event is None:
            return None
        return event.kind.value, event.reminder_index, event.session_id

    def cancel_pending_confirmation(self) -> bool:
        self.delegation.cancel()
        self.routines.cancel()
        with self.routines.lock:
            if self.routines.active:
                self.routines.finish('cancelled')
        return self.confirmation_sessions.cancel()

    def cancel_active_work(self) -> None:
        self.delegation.cancel()
        self.routines.cancel()
        cancelled = getattr(self, "_work_cancelled", None)
        if cancelled is not None:
            cancelled.set()
        if self.organization is not None:
            self.organization.cancel()

    def _check_work_cancelled(self) -> None:
        cancelled = getattr(self, "_work_cancelled", None)
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError("Turno cancelado antes de nova geração ou Tool.")

    def close_confirmation_sessions(self) -> None:
        self.confirmation_sessions.close()

    def close(self) -> None:
        """Encerra coordenadores e sessões pertencentes ao Harness."""
        self.control_center_coordinator.close()
        self.close_confirmation_sessions()

    def _process_active_message(
        self, mensagem: str, response_mode: str = "text"
    ) -> str:
        self._calendar_response_mode = response_mode
        self.last_response_private_calendar = False
        self.last_response_private_google = False
        self.last_briefing_text = ''
        from jarvis.integrations.google.calendar.write_routing import dispatch as calendar_write_dispatch
        calendar_write = calendar_write_dispatch(self, mensagem)
        if calendar_write is not None:
            return calendar_write
        briefing = self.daily_briefing.dispatch(self, mensagem, response_mode)
        if briefing is not None:
            return briefing
        automatic = None
        if (self.config.briefing_enabled and not self.has_pending_action()
                and not mensagem.startswith('/') and self.daily_briefing.auto_due()):
            automatic = self.daily_briefing.render(self, response_mode)
        from jarvis.integrations.google.drive_docs.routing import looks_private_document
        private_tool = self.tool_registry.get('google_drive_find_files')
        if private_tool is not None and not looks_private_document(mensagem):
            private_tool.service.clear()
        response = self.intent_coordinator.process(mensagem, response_mode)
        if automatic is not None:
            self.last_response_private_calendar = True
            self.last_briefing_text += '\n\n' + response
            return automatic + '\n\n' + response
        return response

    def _natural_doctor_response(
        self, message: str, *, response_mode: str = "text"
    ) -> str | None:
        return self.intent_coordinator.natural_doctor_response(
            message, response_mode=response_mode
        )

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
        from jarvis.integrations.google.drive_docs.routing import looks_private_document
        self._current_trace_request = '<private-google>' if looks_private_document(request) else request.strip()

    def _obsidian_fast_response(self, message: str) -> str | None:
        return self.obsidian_coordinator.fast_response(message)

    def _compound_fast_response(self, message: str) -> str | None:
        return self.obsidian_coordinator.compound_fast_response(message)

    def _format_skills(self) -> str:
        return self.skill_coordinator.format_skills()

    def _process_skill_command(self, command: str) -> str:
        return self.skill_coordinator.process_command(
            command,
            last_tool_trace_request=self.last_tool_trace_request,
            last_tool_trace=self.last_tool_trace,
        )

    def _learned_skill_response(self, message: str) -> str | None:
        return self.skill_coordinator.learned_skill_response(message)

    @staticmethod
    def _natural_skill_run_name(message: str) -> str | None:
        return SkillCoordinator.natural_skill_run_name(message)

    def _run_skill(self, identifier: str) -> str:
        return self.skill_coordinator.run_skill(identifier)

    @staticmethod
    def _natural_skill_list_requested(message: str) -> bool:
        return SkillCoordinator.natural_skill_list_requested(message)

    def _natural_skill_list_response(self) -> str:
        return self.skill_coordinator.natural_skill_list_response()

    @staticmethod
    def _natural_skill_learning_name(message: str) -> str | None:
        return SkillCoordinator.natural_skill_learning_name(message)

    @staticmethod
    def _skill_control_fallback(message: str) -> str | None:
        return SkillCoordinator.skill_control_fallback(message)

    def _learn_last_skill(self, name: str) -> str:
        return self.skill_coordinator.learn_last_skill(
            name,
            last_tool_trace_request=self.last_tool_trace_request,
            last_tool_trace=self.last_tool_trace,
        )

    def _mirror_skill(
        self, skill: Any, *, active: bool = True
    ) -> str | None:
        return self.skill_coordinator.mirror_skill(skill, active=active)

    def _obsidian_available(self) -> bool:
        return self.obsidian_coordinator.available()

    def _obsidian_write_available(self) -> bool:
        return self.obsidian_coordinator.write_available()

    def _requests_obsidian_write(self, message: str) -> bool:
        return self.obsidian_coordinator.requests_write(message)

    def _process_config_command(self, command: str) -> str:
        return self.config_command_coordinator.process(command)

    def _process_normal_message(
        self,
        mensagem: str,
        response_mode: str = "text",
        turn_kind: TurnKind = TurnKind.EFFECT_REQUEST,
    ) -> str:
        return self.agent_loop_coordinator.process(
            mensagem, response_mode, turn_kind
        )

    def _generate_llm_response(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        llm_started = time.perf_counter()
        try:
            self._check_work_cancelled()
            from jarvis.llm.ollama_client import OllamaLLM
            if isinstance(self.llm, OllamaLLM):
                self.llm.max_output_tokens = 512 if self.config.assistant_detail == "detalhadas" else 256
            self.response_coordinator.validate_context_snapshot()
            response = self.llm.generate(messages=messages, tools=tools)
            self._check_work_cancelled()
            self.response_coordinator.validate_context_snapshot()
            return response
        finally:
            self.last_latency["llm_seconds"] += (
                time.perf_counter() - llm_started
            )
            self.last_latency["llm_calls"] += 1

    def _execute_tool(
        self,
        tool: Any,
        arguments: dict[str, Any],
        grant: AuthorizationGrant | None = None,
        interaction_id: str | None = None,
    ) -> ToolExecutionResult:
        tool_started = time.perf_counter()
        try:
            self._check_work_cancelled()
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
                    result = execute_with_result(
                        tool,
                        _interaction_id=interaction_id,
                        **arguments,
                    )
                else:
                    content = self.tool_executor.execute(tool, **arguments)
                    result = ToolExecutionResult(str(content), "success", True)
            is_calendar_tool = tool.name.startswith("google_calendar_")
            has_private_arguments = is_calendar_tool or tool.name in {"safe_web_search", 'local_briefing_memory'} or tool.name.startswith(('google_drive_', 'google_docs_'))
            has_private_arguments = has_private_arguments or getattr(self.tool_executor, "permissions", None) is not None
            if result.status == "success" and not has_private_arguments:
                self._current_tool_trace.append(
                    ToolTraceStep(tool.name, dict(arguments))
                )
            self._voice_trace(
                "tool_executed", tool=tool.name, status=result.status,
                authorized=result.authorized,
            )
            if self.config.latency_debug_enabled:
                safe_arguments = {
                    key: (
                        "<redacted>"
                        if has_private_arguments or key in {"content", "text", "term"}
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

    def _request_critical_confirmation(
        self,
        tool: Any,
        target: str,
        arguments: dict[str, Any],
        interaction_id: str | None,
        preparation_digest: str | None = None,
    ) -> ToolExecutionResult:
        risk = tool.risk
        permissions = getattr(self.tool_executor, "permissions", None)
        if permissions is not None and permissions.check_tool(tool, arguments).mode == "ask":
            risk = max(risk, RiskLevel.EXTERNAL_EFFECT)
        prompt = security_prompt(tool.name, target, risk=risk)
        custom_prompt = getattr(tool, "confirmation_prompt", None)
        if callable(custom_prompt):
            preparation = tool.prepare(**arguments)
            if preparation.binding_digest != preparation_digest:
                return ToolExecutionResult(
                    "A ação mudou antes da confirmação.",
                    "security_blocked",
                    False,
                )
            prompt = str(custom_prompt(preparation))
        if tool.name == "local_application_close":
            prompt = (
                f"Vou fechar {target}. Trabalho não salvo pode ser perdido. "
                "Autoriza o fechamento gracioso desta instância?"
            )
        session = self.confirmation_sessions.request(
            tool_name=tool.name,
            target=target,
            arguments=arguments,
            risk=risk,
            interaction_id=interaction_id,
            prompt_text=prompt,
            preparation_digest=preparation_digest,
            timeout_seconds=getattr(tool, "confirmation_timeout_seconds", None),
        )
        if session is None:
            return ToolExecutionResult(
                "Já existe uma ação crítica aguardando confirmação. "
                "Confirme ou cancele essa ação antes de iniciar outra.",
                "confirmation_required",
                False,
            )
        return ToolExecutionResult(
            session.prompt_text,
            "confirmation_required",
            False,
        )

    def _process_confirmation_message(
        self,
        message: str,
        session_id: str | None,
        challenge_id: str | None,
    ) -> str | None:
        with self.routines.lock:
            if self.routines.active and not self.routines.confirmation_valid():
                self.confirmation_sessions.cancel()
                return self.routines.finish('cancelled')
            response = self._process_confirmation_message_core(message, session_id, challenge_id)
            if self.routines.active and self.confirmation_sessions.current() is None:
                return self.routines.finish('interrupted')
            return response

    def _process_confirmation_message_core(
        self,
        message: str,
        session_id: str | None,
        challenge_id: str | None,
    ) -> str | None:
        session = self.confirmation_sessions.current()
        if session is None:
            if session_id is not None or challenge_id is not None:
                return "Essa confirmação não está mais ativa."
            return None

        if session.invocation.tool_name == self.delegation.tool.name:
            self.last_response_private_calendar = True

        normalized = self.confirmation_interpreter.normalize(message)
        if re.search(
            r"\b(?:o que voce vai fazer|o que vai fazer|qual e a acao|"
            r"qual acao|qual e o alvo|qual alvo)\b",
            normalized,
        ):
            return (
                f"A ação pendente é {session.invocation.tool_name} sobre "
                f"{session.invocation.target}. Ela ainda não foi executada."
            )
        if re.search(
            r"\b(?:por que|porque|explique|detalhes|por qual motivo)\b",
            normalized,
        ):
            return (
                "Preciso confirmar porque essa ação foi classificada como "
                "CRITICAL. Nada será executado até uma autorização válida."
            )

        interpreted = self.confirmation_interpreter.interpret_result(
            message,
            ConfirmationPurpose.SECURITY,
        )
        denied = (
            interpreted.denied
            or self.cancellation_interpreter.is_cancellation(message)
        )
        confirmed = interpreted.action_authorized and not denied
        if not confirmed and not denied:
            if self.turn_policy.classify(message) is TurnKind.ADVISORY:
                return None
            return (
                "Ainda estou aguardando uma confirmação clara. "
                "Diga sim para continuar ou cancelar para interromper."
            )

        decision, decided_session, grant = self.confirmation_sessions.decide(
            confirmed=confirmed,
            denied=denied,
            session_id=session_id,
            challenge_id=challenge_id,
            grant_factory=self._grant_confirmation_session,
        )
        if decision is ConfirmationDecision.STALE:
            return "Essa resposta não corresponde à confirmação atual."
        if decision is ConfirmationDecision.DENIED:
            return "Ação crítica cancelada."
        if (
            decision is not ConfirmationDecision.CONFIRMED
            or decided_session is None
            or grant is None
        ):
            return "Ainda estou aguardando sua confirmação."

        invocation = decided_session.invocation
        tool = self.tool_registry.get(invocation.tool_name)
        if tool is None and invocation.tool_name == "memory_forget":
            tool = getattr(self, "_memory_forget_tool", None)
        if tool is None and invocation.tool_name == self.delegation.tool.name:
            tool = self.delegation.tool
        arguments = dict(invocation.arguments)
        if (
            tool is None
            or digest_arguments(arguments) != invocation.arguments_digest
        ):
            self._cleanup_confirmation_session(decided_session)
            return "A ação confirmada não pôde ser revalidada e foi cancelada."
        valid, _error = self.argument_validator.validate(tool, arguments)
        if not valid:
            self._cleanup_confirmation_session(decided_session)
            return "A ação confirmada não pôde ser revalidada e foi cancelada."
        result = self._execute_tool(
            tool,
            arguments,
            grant,
            invocation.interaction_id,
        )
        self._cleanup_confirmation_session(decided_session)
        if self.routines.active:
            return self.routines.resume(result)
        return result.content

    def _grant_confirmation_session(
        self,
        session: ConfirmationSession,
    ) -> AuthorizationGrant | None:
        invocation = session.invocation
        return self.tool_executor.safety_manager.grant_explicit_authorization(
            invocation.tool_name,
            invocation.target,
            invocation.risk,
            invocation.interaction_id,
            session.id,
            invocation.arguments_digest,
            invocation.preparation_digest,
        )

    def _cleanup_confirmation_session(
        self,
        session: ConfirmationSession,
    ) -> None:
        invocation = session.invocation
        self.pending_actions.clear_if_interaction(invocation.interaction_id)
        self.tool_executor.safety_manager.invalidate_session(session.id)
        if (invocation.tool_name == self.delegation.tool.name
                and session.state in {ConfirmationSessionState.DENIED, ConfirmationSessionState.EXPIRED}):
            self.delegation.invalidate()

    def _execute_compound_follow_up(
        self,
        message: str,
        *,
        completed_tool: str,
        completed_arguments: dict[str, Any],
        already_requested: set[str],
    ) -> tuple[str, dict[str, Any], ToolExecutionResult] | None:
        return self.tool_execution_coordinator.execute_compound_follow_up(
            message,
            completed_tool=completed_tool,
            completed_arguments=completed_arguments,
            already_requested=already_requested,
        )

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
        return self.tool_execution_coordinator.resolve_file_reference(
            tool, arguments
        )

    def _remember_operational_context(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_content: str = "",
    ) -> None:
        self.tool_execution_coordinator.remember_operational_context(
            tool_name, arguments, result_content
        )

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
        return self.tool_execution_coordinator.resolve_note_reference(
            tool, arguments
        )

    @staticmethod
    def _missing_target_field(arguments: dict[str, Any]) -> str | None:
        return ToolExecutionCoordinator.missing_target_field(arguments)

    def _process_pending_message(self, message: str) -> str | None:
        return self.tool_execution_coordinator.process_pending_message(message)

    def _resolve_and_execute_pending(
        self,
        pending: PendingAction,
        spoken_target: str,
    ) -> str:
        return self.tool_execution_coordinator.resolve_and_execute_pending(
            pending, spoken_target
        )

    def _grant_compound_authorization(
        self,
        tool: Any,
        arguments: dict[str, Any],
        interaction_id: str,
    ) -> AuthorizationGrant | None:
        return self.tool_execution_coordinator.grant_compound_authorization(
            tool, arguments, interaction_id
        )

    def _cancel_pending_action(self) -> None:
        self.tool_execution_coordinator.cancel_pending_action()

    def _system_context(
        self, response_mode: str = "text", user_message: str | None = None
    ) -> list[dict[str, Any]]:
        return self.response_coordinator.system_context(response_mode, user_message)

    def _append_recent_context(
        self, lines: list[str], active: ActiveContext
    ) -> None:
        self.response_coordinator.append_recent_context(lines, active)

    @staticmethod
    def _tool_matches_context(name: str, active: ActiveContext) -> bool:
        return ResponseCoordinator.tool_matches_context(name, active)

    def _route_tool_call(self, message: str, tool_call: Any) -> None:
        self.response_coordinator.route_tool_call(message, tool_call)

    def _search_query(self, message: str) -> str:
        return self.response_coordinator.search_query(message)

    def _finalize_llm_response(
        self,
        content: str,
        messages: list[dict[str, Any]],
        *,
        tool_executed: bool = False,
        successful_tool_names: set[str] | None = None,
    ) -> str:
        return self.response_coordinator.finalize_llm_response(
            content,
            messages,
            tool_executed=tool_executed,
            successful_tool_names=successful_tool_names or (),
        )

    def _fast_path_response(self, message: str) -> str | None:
        from jarvis.integrations.google.drive_docs.routing import dispatch
        private_response = dispatch(self, message)
        if private_response is not None:
            return private_response
        from jarvis.integrations.google.calendar.read_routing import looks_like_calendar_read, read_request
        if looks_like_calendar_read(message):
            tool = self.tool_registry.get("google_calendar_list_events")
            if tool is not None and hasattr(tool, "calendar_aliases"):
                request = read_request(message)
                if request is None:
                    return "Peça somente agenda de hoje, amanhã ou próximos sete dias; sem negação ou duas intenções."
                period, alias = request
                try:
                    calendar_id = tool.resolve_calendar(alias)
                except PermissionError:
                    return "Calendário não autorizado. Configure um alias explícito antes de consultar."
                result = self._execute_tool(tool, {"period": period, "calendar_id": calendar_id})
                self.last_response_private_calendar = True
                # Never put private events in ConversationMemory / LLM context.
                if result.status == "success" and getattr(self, "_calendar_response_mode", "text") == "voice":
                    lines = result.content.splitlines()
                    if len(lines) > 1:
                        return ("\n".join(lines[:3])[:450] + " Detalhes adicionais por texto: repita a consulta.")
                return result.content
            if self.fast_path_router.match(message) is None:
                return "Não consultei a agenda. Informe um único período e calendário autorizado."
        last_window = self.recent_context.get("last_desktop_window")
        desktop_action = self.tool_selection_router.desktop_action(
            message,
            last_window if isinstance(last_window, str) else None,
        )
        if desktop_action is not None:
            tool_name, arguments = desktop_action
            tool = self.tool_registry.get(tool_name)
            if tool is None:
                if self.tool_selection_router.is_desktop_reference(message):
                    response = (
                        "O controle de desktop não está disponível nesta execução."
                    )
                    self.memory.add_user_message(message)
                    self.memory.add_assistant_message(response)
                    return response
            else:
                self.memory.add_user_message(message)
                result = self._execute_tool(tool, arguments)
                if result.status == "success":
                    self._remember_operational_context(
                        tool.name,
                        arguments,
                        result.content,
                    )
                self.memory.add_assistant_message(result.content)
                return result.content
        if self.tool_selection_router.is_desktop_reference(message):
            response = (
                "Não tenho uma janela recente inequívoca para essa referência. "
                "Diga o título da janela que deseja focar."
            )
            self.memory.add_user_message(message)
            self.memory.add_assistant_message(response)
            return response

        if self.tool_selection_router.is_incomplete_desktop_input(message):
            response = (
                "Diga em qual janela devo pressionar Escape; não vou "
                "inferir o alvo."
            )
            self.memory.add_user_message(message)
            self.memory.add_assistant_message(response)
            return response

        if self.tool_selection_router.is_vision_request(message):
            tool = self.tool_registry.get("vision_observe_screen_change")
            if tool is None:
                response = "Vision read-only não está disponível nesta execução."
                self.memory.add_user_message(message)
                self.memory.add_assistant_message(response)
                return response
            self.memory.add_user_message(message)
            result = self._execute_tool(tool, {})
            if result.status == "success":
                self._remember_operational_context(
                    tool.name,
                    {},
                    result.content,
                )
            self.memory.add_assistant_message(result.content)
            return result.content

        match = self.fast_path_router.match(message)
        self._voice_trace(
            "fast_intent", intent=match.intent.name if match is not None else None,
        )
        if match is None:
            return None

        # Handle Calendar fast paths
        if match.intent in {
            FastIntent.CALENDAR_TODAY,
            FastIntent.CALENDAR_TOMORROW,
            FastIntent.CALENDAR_UPCOMING,
            FastIntent.CALENDAR_WEEKDAY,
            FastIntent.CALENDAR_NEXT_WEEK,
            FastIntent.CALENDAR_AVAILABILITY,
        }:
            availability = match.intent is FastIntent.CALENDAR_AVAILABILITY
            tool = self.tool_registry.get(
                "google_calendar_check_availability"
                if availability
                else "google_calendar_list_events"
            )
            if tool is None:
                return "Google Calendar não está disponível nesta execução."
            arguments = {"period": match.period or "today", "calendar_id": "primary"}
            if availability:
                arguments.update(
                    start_time=match.start_time,
                    end_time=match.end_time,
                )
            result = self._execute_tool(tool, arguments)
            if result.status != "success":
                return result.content
            return result.content

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
        return self.response_coordinator.note_search_suggestion(query)

    def _is_ambiguous_note_cleanup(self, message: str) -> bool:
        return self.response_coordinator.is_ambiguous_note_cleanup(message)
