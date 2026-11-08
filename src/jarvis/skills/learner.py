from __future__ import annotations

from jarvis.security.risk import RiskLevel
from jarvis.skills.models import SkillDefinition, SkillStep, ToolTraceStep
from jarvis.tools.registry import ToolRegistry


class SkillLearner:
    """Turns an already-successful execution trace into a reusable procedure.

    No LLM-authored arbitrary action is trusted here. Only tool calls that were
    actually executed successfully can become a learned Skill.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry

    def from_trace(
        self,
        name: str,
        original_request: str,
        trace: list[ToolTraceStep],
    ) -> SkillDefinition:
        clean_name = " ".join(name.split())
        if not clean_name:
            raise ValueError("Informe um nome para a Skill.")
        if not trace:
            raise ValueError("Não há um procedimento recente para aprender.")
        steps: list[SkillStep] = []
        for item in trace:
            tool = self.tool_registry.get(item.tool_name)
            if tool is None:
                raise ValueError(f"Tool '{item.tool_name}' não está disponível.")
            if tool.risk >= RiskLevel.DESTRUCTIVE:
                raise ValueError("Ações CRITICAL não podem virar Skills.")
            steps.append(SkillStep(item.tool_name, dict(item.arguments)))
        triggers = (original_request.strip(),) if original_request.strip() else ()
        return SkillDefinition(
            name=clean_name,
            description=(
                "Procedimento aprendido a partir de uma execução real e "
                "bem-sucedida do Jarvis."
            ),
            original_request=original_request.strip(),
            triggers=triggers,
            steps=tuple(steps),
        )
