"""Coordenação de Skills fora do Harness principal."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from jarvis.skills.executor import SkillExecutor
from jarvis.skills.learner import SkillLearner
from jarvis.skills.matcher import SkillMatcher
from jarvis.skills.mirror import ObsidianSkillMirror
from jarvis.skills.models import ToolTraceStep
from jarvis.skills.store import SkillStore


RememberOperationalContext = Callable[[str, dict[str, Any], str], None]


class SkillCoordinator:
    """Orquestra aprendizado, resolução e execução de Skills.

    O coordenador não altera a política de segurança: cada etapa continua sendo
    executada pelo ``SkillExecutor`` e, por consequência, pelo ``ToolExecutor``
    configurado pelo Core.
    """

    def __init__(
        self,
        skill_store: SkillStore,
        skill_learner: SkillLearner,
        skill_matcher: SkillMatcher,
        skill_executor: SkillExecutor,
        skill_mirror: ObsidianSkillMirror | None,
        remember_operational_context: RememberOperationalContext,
    ) -> None:
        self.skill_store = skill_store
        self.skill_learner = skill_learner
        self.skill_matcher = skill_matcher
        self.skill_executor = skill_executor
        self.skill_mirror = skill_mirror
        self.remember_operational_context = remember_operational_context

    def format_skills(self) -> str:
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

    def process_command(
        self,
        command: str,
        *,
        last_tool_trace_request: str,
        last_tool_trace: list[ToolTraceStep],
    ) -> str:
        if command.strip().casefold() == "/skill":
            return (
                "Uso: /skills | /skill learn <nome> | /skill show <id|nome> | "
                "/skill run <id|nome> | /skill forget <id|nome> | /skill sync"
            )
        learn = re.fullmatch(
            r'/skill\s+learn\s+"?(.+?)"?', command, flags=re.IGNORECASE
        )
        if learn is not None:
            return self.learn_last_skill(
                learn.group(1),
                last_tool_trace_request=last_tool_trace_request,
                last_tool_trace=last_tool_trace,
            )
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
            return self.run_skill(run.group(1))
        forget = re.fullmatch(
            r'/skill\s+forget\s+"?(.+?)"?', command, flags=re.IGNORECASE
        )
        if forget is not None:
            skill = self.skill_store.delete(forget.group(1))
            if skill is None:
                return "Skill não encontrada."
            self.mirror_skill(skill, active=False)
            return f"Skill '{skill.name}' removida do registro executável."
        if re.fullmatch(r"/skill\s+sync", command, flags=re.IGNORECASE):
            if self.skill_mirror is None:
                return "Espelho de Skills no Obsidian não está disponível."
            count = 0
            for skill in self.skill_store.list():
                self.mirror_skill(skill)
                count += 1
            return f"{count} Skill(s) sincronizada(s) com o Obsidian."
        return "Comando de Skill inválido. Use /skill para ver as opções."

    def learned_skill_response(self, message: str) -> str | None:
        try:
            skill = self.skill_matcher.match(message, self.skill_store.list())
        except RuntimeError:
            return None
        if skill is None:
            return None
        return self.run_skill(skill.id)

    @staticmethod
    def natural_skill_run_name(message: str) -> str | None:
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

    def run_skill(self, identifier: str) -> str:
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
                self.remember_operational_context(
                    step.tool_name, step.arguments, execution.content
                )
        skill.mark_execution(result.success)
        self.skill_store.save(skill)
        self.mirror_skill(skill)
        if not result.success:
            return result.message
        if result.results and skill.steps[-1].tool_name in {
            "obsidian_read_note", "read_file", "get_datetime",
            "get_runtime_info",
        }:
            return result.results[-1].content
        return "Pronto."

    @staticmethod
    def natural_skill_list_requested(message: str) -> bool:
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

    def natural_skill_list_response(self) -> str:
        try:
            skills = self.skill_store.list()
        except RuntimeError as error:
            return f"Não foi possível carregar as Skills: {error}"
        if not skills:
            return "Ainda não tenho nenhuma Skill aprendida."
        names = ", ".join(skill.name for skill in skills)
        return f"Tenho estas Skills: {names}."

    @staticmethod
    def natural_skill_learning_name(message: str) -> str | None:
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
    def skill_control_fallback(message: str) -> str | None:
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

    def learn_last_skill(
        self,
        name: str,
        *,
        last_tool_trace_request: str,
        last_tool_trace: list[ToolTraceStep],
    ) -> str:
        try:
            existing = self.skill_store.get(name)
            if existing is not None:
                return (
                    f"Já existe uma Skill chamada '{existing.name}'. "
                    "Remova-a primeiro se quiser reaprender."
                )
            skill = self.skill_learner.from_trace(
                name, last_tool_trace_request, last_tool_trace
            )
            self.skill_store.save(skill)
            mirror_path = self.mirror_skill(skill)
        except (RuntimeError, ValueError) as error:
            return str(error)
        mirror = f" Espelho criado em {mirror_path}." if mirror_path else ""
        return (
            f"Skill '{skill.name}' aprendida com {len(skill.steps)} etapa(s)."
            f"{mirror}"
        )

    def mirror_skill(self, skill: Any, *, active: bool = True) -> str | None:
        mirror = self.skill_mirror
        if mirror is None:
            return None
        try:
            return mirror.sync(skill, active=active)
        except (OSError, PermissionError, ValueError):
            return None
