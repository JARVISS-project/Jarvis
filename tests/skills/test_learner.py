import pytest

from jarvis.security.risk import RiskLevel
from jarvis.skills.learner import SkillLearner
from jarvis.skills.models import ToolTraceStep
from jarvis.tools.base import Tool
from jarvis.tools.registry import ToolRegistry


class LowTool(Tool):
    name = "low_tool"
    description = "low"
    risk = RiskLevel.LOW
    parameters = {"type": "object", "properties": {}, "required": []}

    def get_target(self, **_kwargs):  # type: ignore[no-untyped-def]
        return "target"

    def execute(self, **_kwargs):  # type: ignore[no-untyped-def]
        return "ok"


class CriticalTool(LowTool):
    name = "critical_tool"
    risk = RiskLevel.CRITICAL


def test_learner_only_uses_successful_registered_trace() -> None:
    registry = ToolRegistry()
    registry.register(LowTool())
    learner = SkillLearner(registry)

    skill = learner.from_trace(
        "Minha rotina",
        "faça isso",
        [ToolTraceStep("low_tool", {})],
    )

    assert skill.name == "Minha rotina"
    assert skill.steps[0].tool_name == "low_tool"
    assert skill.triggers == ("faça isso",)


def test_learner_refuses_critical_action() -> None:
    registry = ToolRegistry()
    registry.register(CriticalTool())
    learner = SkillLearner(registry)

    with pytest.raises(ValueError, match="CRITICAL"):
        learner.from_trace(
            "Perigosa", "faça", [ToolTraceStep("critical_tool", {})]
        )
