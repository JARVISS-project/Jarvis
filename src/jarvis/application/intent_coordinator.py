"""Coordena roteamento de alto nível antes do agent loop."""

from __future__ import annotations

from collections.abc import Callable
import re

from jarvis.config import JarvisConfig
from jarvis.context.active import ActiveContext, ContextRouter
from jarvis.doctor import JarvisDoctor
from jarvis.llm.base import LLMClient
from jarvis.runtime.resource_manager import ResourceManager

from jarvis.application.obsidian_coordinator import ObsidianCoordinator
from jarvis.application.response_coordinator import ResponseCoordinator
from jarvis.application.skill_coordinator import SkillCoordinator
from jarvis.application.tool_execution_coordinator import ToolExecutionCoordinator


MessageHandler = Callable[[str], str | None]
NormalMessageHandler = Callable[[str, str], str]


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
        fast_path_response: MessageHandler,
        runtime_info_response: MessageHandler,
        normal_message: NormalMessageHandler,
        get_last_tool_trace: Callable[[], tuple[str, list]],
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
        self.fast_path_response = fast_path_response
        self.runtime_info_response = runtime_info_response
        self.normal_message = normal_message
        self.get_last_tool_trace = get_last_tool_trace

    def process(self, message: str, response_mode: str = "text") -> str:
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
        fast_response = self.fast_path_response(message)
        if fast_response is not None:
            return fast_response
        learned_response = self.skill_coordinator.learned_skill_response(message)
        if learned_response is not None:
            return learned_response

        previous_context = self.context_router.state.active
        routed_context = self.context_router.update(message)
        if (
            routed_context is ActiveContext.OBSIDIAN
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
            routed_context is ActiveContext.OBSIDIAN
            and self.obsidian_coordinator.requests_write(message)
            and not self.obsidian_coordinator.write_available()
        ):
            return "A escrita no Obsidian está desabilitada ou indisponível."

        obsidian_response = self.obsidian_coordinator.fast_response(message)
        if obsidian_response is not None:
            return obsidian_response
        compound_response = self.obsidian_coordinator.compound_fast_response(message)
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

        keep_alive = (
            self.config.ollama_keep_alive_game_mode
            if self.resource_manager.is_game_mode_active()
            else self.config.ollama_keep_alive_normal
        )
        self.llm.set_keep_alive(keep_alive)
        return self.normal_message(message, response_mode)

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
