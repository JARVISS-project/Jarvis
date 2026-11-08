"""Coordena roteamento de alto nível antes do agent loop."""

from __future__ import annotations

from collections.abc import Callable
import re

from jarvis.config import JarvisConfig
from jarvis.context.active import ActiveContext, ContextRouter
from jarvis.doctor import JarvisDoctor
from jarvis.llm.base import LLMClient
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.tool_selection import ToolSelectionRouter

from jarvis.application.obsidian_coordinator import ObsidianCoordinator
from jarvis.application.semantic_read_dispatcher import SemanticReadDispatcher
from jarvis.application.local_control_dispatcher import LocalControlDispatcher
from jarvis.application.safe_browser_dispatcher import SafeBrowserDispatcher
from jarvis.application.response_coordinator import ResponseCoordinator
from jarvis.application.skill_coordinator import SkillCoordinator
from jarvis.application.tool_execution_coordinator import ToolExecutionCoordinator
from jarvis.application.turn_policy import TurnKind, TurnPolicy


MessageHandler = Callable[[str], str | None]
NormalMessageHandler = Callable[[str, str, TurnKind], str]


class IntentCoordinator:
    """Seleciona o caminho de intenção sem acoplar subsistemas ao Harness."""

    def __init__(
        self,
        config: JarvisConfig,
        resource_manager: ResourceManager,
        llm: LLMClient,
        context_router: ContextRouter,
        doctor: JarvisDoctor,
        skill_coordinator: SkillCoordinator,
        obsidian_coordinator: ObsidianCoordinator,
        tool_execution_coordinator: ToolExecutionCoordinator,
        response_coordinator: ResponseCoordinator,
        turn_policy: TurnPolicy,
        fast_path_response: MessageHandler,
        runtime_info_response: MessageHandler,
        normal_message: NormalMessageHandler,
        get_last_tool_trace: Callable[[], tuple[str, list]],
        semantic_read_dispatcher: SemanticReadDispatcher | None = None,
        local_control_dispatcher: LocalControlDispatcher | None = None,
        safe_browser_dispatcher: SafeBrowserDispatcher | None = None,
        application_dispatcher=None,
    ) -> None:
        self.config = config
        self.resource_manager = resource_manager
        self.llm = llm
        self.context_router = context_router
        self.doctor = doctor
        self.skill_coordinator = skill_coordinator
        self.obsidian_coordinator = obsidian_coordinator
        self.tool_execution_coordinator = tool_execution_coordinator
        self.response_coordinator = response_coordinator
        self.turn_policy = turn_policy
        self.fast_path_response = fast_path_response
        self.runtime_info_response = runtime_info_response
        self.normal_message = normal_message
        self.get_last_tool_trace = get_last_tool_trace
        self.semantic_read_dispatcher = semantic_read_dispatcher
        self.local_control_dispatcher = local_control_dispatcher
        self.safe_browser_dispatcher = safe_browser_dispatcher
        self.application_dispatcher = application_dispatcher

    def process(self, message: str, response_mode: str = "text") -> str:
        from jarvis.integrations.google.calendar.read_routing import looks_like_calendar_read
        from jarvis.integrations.google.drive_docs.routing import looks_private_document
        if looks_like_calendar_read(message) or looks_private_document(message):
            calendar = self.fast_path_response(message)
            if calendar is not None:
                return calendar
        turn_kind = self.turn_policy.classify(message)
        if turn_kind is TurnKind.ADVISORY:
            cancellation = (
                self.tool_execution_coordinator.process_pending_cancellation(
                    message
                )
            )
            if cancellation is not None:
                return cancellation
            return self._normal_message(message, response_mode, turn_kind)

        natural_doctor = self.natural_doctor_response(
            message, response_mode=response_mode
        )
        if natural_doctor is not None:
            return natural_doctor

        if self.skill_coordinator.natural_skill_list_requested(message):
            return self.skill_coordinator.natural_skill_list_response()
        natural_skill_run = self.skill_coordinator.natural_skill_run_name(message)
        if natural_skill_run is not None:
            return self.skill_coordinator.run_skill(natural_skill_run)
        natural_skill_name = self.skill_coordinator.natural_skill_learning_name(
            message
        )
        if natural_skill_name is not None:
            last_request, last_trace = self.get_last_tool_trace()
            return self.skill_coordinator.learn_last_skill(
                natural_skill_name,
                last_tool_trace_request=last_request,
                last_tool_trace=last_trace,
            )
        skill_control_fallback = self.skill_coordinator.skill_control_fallback(
            message
        )
        if skill_control_fallback is not None:
            return skill_control_fallback

        if self.response_coordinator.is_ambiguous_note_cleanup(message):
            self.tool_execution_coordinator.cancel_pending_action()
            return (
                "Posso listar as notas que parecem ser de teste e você "
                "escolhe quais deseja revisar ou remover. Qual escopo devo usar?"
            )
        if self.safe_browser_dispatcher is not None:
            browser = self.safe_browser_dispatcher.dispatch(message)
            if browser.handled:
                return browser.response or "Não executei nenhuma abertura no navegador."
        if self.local_control_dispatcher is not None:
            local = self.local_control_dispatcher.dispatch(message)
            if local.handled:
                return local.response or "Não executei nenhum controle local."
        if self.application_dispatcher is not None:
            application = self.application_dispatcher.dispatch(message)
            if application.handled:
                return application.response or 'Pedido de aplicativo bloqueado: nome não descoberto, ambiguidade, negação ou texto residual.'
        if self.semantic_read_dispatcher is not None:
            semantic = self.semantic_read_dispatcher.dispatch(message)
            if semantic.handled:
                return semantic.response or "Não executei nenhuma Tool."
        fast_response = self.fast_path_response(message)
        if fast_response is not None:
            return fast_response
        learned_response = self.skill_coordinator.learned_skill_response(message)
        if learned_response is not None:
            return learned_response

        previous_context = self.context_router.state.active
        routed_context = self.context_router.update(message)
        desktop_request = ToolSelectionRouter.is_desktop_request(message)
        if (
            not desktop_request
            and routed_context is ActiveContext.OBSIDIAN
            and not self.obsidian_coordinator.available()
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
            not desktop_request
            and routed_context is ActiveContext.OBSIDIAN
            and self.obsidian_coordinator.requests_write(message)
            and not self.obsidian_coordinator.write_available()
        ):
            return "A escrita no Obsidian está desabilitada ou indisponível."

        if not desktop_request:
            obsidian_response = self.obsidian_coordinator.fast_response(message)
            if obsidian_response is not None:
                return obsidian_response
            compound_response = self.obsidian_coordinator.compound_fast_response(
                message
            )
            if compound_response is not None:
                return compound_response
        runtime_response = self.runtime_info_response(message)
        if runtime_response is not None:
            return runtime_response
        pending_response = self.tool_execution_coordinator.process_pending_message(
            message
        )
        if pending_response is not None:
            return pending_response

        return self._normal_message(message, response_mode, turn_kind)

    def _normal_message(
        self,
        message: str,
        response_mode: str,
        turn_kind: TurnKind,
    ) -> str:
        keep_alive = (
            self.config.ollama_keep_alive_game_mode
            if self.resource_manager.is_game_mode_active()
            else self.config.ollama_keep_alive_normal
        )
        self.llm.set_keep_alive(keep_alive)
        return self.normal_message(message, response_mode, turn_kind)

    def natural_doctor_response(
        self,
        message: str,
        *,
        response_mode: str = "text",
    ) -> str | None:
        normalized = self.context_router.normalize(message)
        if re.search(r"\b(?:doctor|doutor)\b", normalized) is None:
            return None
        if re.search(
            r"\b(?:rode|roda|rodar|execute|executa|executar|verifique|"
            r"verifica|cheque|checa|mostre|mostra|fale|fala|falar|diga|"
            r"diz|dizer|explique|explica|explicar|conte|conta|contar|"
            r"barra|status|diagnostico)\b",
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
