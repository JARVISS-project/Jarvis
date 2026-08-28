from typing import Any
from pathlib import Path
import re
import time

from jarvis.security.authorization import AuthorizationGrant

from jarvis.application.agent_loop_coordinator import AgentLoopCoordinator
from jarvis.application.intent_coordinator import IntentCoordinator
from jarvis.application.obsidian_coordinator import ObsidianCoordinator
from jarvis.application.response_coordinator import ResponseCoordinator
from jarvis.application.skill_coordinator import SkillCoordinator
from jarvis.application.tool_execution_coordinator import ToolExecutionCoordinator
from jarvis.application.turn_policy import TurnKind, TurnPolicy

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
        )
        self._latency_turn_started_at: float | None = None

    def run(self) -> None:
        print("Jarvis iniciado.")
        print("Digite 'sair' para encerrar.\n")

        try:
            while True:
                mensagem = input("Você: ")

                if mensagem.lower() == "sair":
                    print("Jarvis: Até mais.")
                    break

                resposta = self.processar_mensagem(mensagem)
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
        stripped_message = mensagem.strip()
        command = stripped_message.lower()

        confirmation_response = self._process_confirmation_message(
            stripped_message,
            confirmation_session_id,
            confirmation_challenge_id,
        )
        if confirmation_response is not None:
            return confirmation_response

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
        return (
            self.pending_actions.has_pending()
            or self.confirmation_sessions.current() is not None
        )

    def has_pending_confirmation(self) -> bool:
        return self.confirmation_sessions.current() is not None

    def current_confirmation_context(self) -> tuple[str, str] | None:
        return self.confirmation_sessions.context()

    def confirmation_state(self) -> str | None:
        state = self.confirmation_sessions.state()
        return state.value if state is not None else None

    def pop_confirmation_event(self) -> tuple[str, int, str] | None:
        event = self.confirmation_sessions.pop_event()
        if event is None:
            return None
        return event.kind.value, event.reminder_index, event.session_id

    def cancel_pending_confirmation(self) -> bool:
        return self.confirmation_sessions.cancel()

    def close_confirmation_sessions(self) -> None:
        self.confirmation_sessions.close()

    def _process_active_message(
        self, mensagem: str, response_mode: str = "text"
    ) -> str:
        return self.intent_coordinator.process(mensagem, response_mode)

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
        self._current_trace_request = request.strip()

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

        if normalized.casefold() == "/config desktop":
            read = "ENABLED" if self.config.desktop_read_enabled else "DISABLED"
            control = (
                "ENABLED" if self.config.desktop_control_enabled else "DISABLED"
            )
            return (
                f"Desktop read: {read}\n"
                f"Desktop control: {control}\n"
                "Kill switch: Ctrl+Alt+K (quando control está habilitado)"
            )

        desktop_match = re.fullmatch(
            r"/config\s+desktop\s+(read|control)\s+(on|off)",
            normalized,
            flags=re.IGNORECASE,
        )
        if desktop_match is not None:
            setting = desktop_match.group(1).casefold()
            enabled = desktop_match.group(2).casefold() == "on"
            try:
                if setting == "read":
                    self.config.save_desktop_settings(read=enabled)
                else:
                    self.config.save_desktop_settings(control=enabled)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            state = "habilitado" if enabled else "desabilitado"
            return (
                f"Desktop {setting} {state}. Reinicie o Jarvis para aplicar."
            )

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
            "Uso: /config | /config obsidian | /config desktop | "
            "/config voice | /config persona | "
            '/config obsidian vault "C:\\caminho" | '
            "/config obsidian write on|off | "
            "/config desktop read on|off | "
            "/config desktop control on|off | "
            "/config voice provider auto|sapi|piper|chatterbox | "
            "/config voice mode fast|quality|auto | "
            "/config voice pace fast|natural|calm | "
            '/config voice reference "C:\\voz.wav"|off | '
            "/config persona treatment senhor|natural|direto"
        )

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
            return self.llm.generate(messages=messages, tools=tools)
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

    def _request_critical_confirmation(
        self,
        tool: Any,
        target: str,
        arguments: dict[str, Any],
        interaction_id: str | None,
    ) -> ToolExecutionResult:
        session = self.confirmation_sessions.request(
            tool_name=tool.name,
            target=target,
            arguments=arguments,
            risk=tool.risk,
            interaction_id=interaction_id,
            prompt_text=security_prompt(tool.name, target, risk=tool.risk),
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
        session = self.confirmation_sessions.current()
        if session is None:
            if session_id is not None or challenge_id is not None:
                return "Essa confirmação não está mais ativa."
            return None

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
        )

    def _cleanup_confirmation_session(
        self,
        session: ConfirmationSession,
    ) -> None:
        invocation = session.invocation
        self.pending_actions.clear_if_interaction(invocation.interaction_id)
        self.tool_executor.safety_manager.invalidate_session(session.id)

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
        self, response_mode: str = "text"
    ) -> list[dict[str, Any]]:
        return self.response_coordinator.system_context(response_mode)

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
    ) -> str:
        return self.response_coordinator.finalize_llm_response(
            content,
            messages,
            tool_executed=tool_executed,
        )

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
        return self.response_coordinator.note_search_suggestion(query)

    def _is_ambiguous_note_cleanup(self, message: str) -> bool:
        return self.response_coordinator.is_ambiguous_note_cleanup(message)
