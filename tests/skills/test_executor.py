from jarvis.security.risk import RiskLevel
from jarvis.skills.executor import SkillExecutor
from jarvis.skills.models import SkillDefinition, SkillStep
from jarvis.tools.base import Tool
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.registry import ToolRegistry


class LowTool(Tool):
    name = "low_tool"
    description = "low"
    risk = RiskLevel.REVERSIBLE_LOCAL
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    def get_target(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs["value"]

    def execute(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs["value"]


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def execute_with_result(self, tool, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((tool.name, kwargs))
        return ToolExecutionResult("ok", "success", True)


def test_skill_executor_reuses_normal_tool_executor_path() -> None:
    registry = ToolRegistry()
    registry.register(LowTool())
    executor = RecordingExecutor()
    runner = SkillExecutor(registry, executor)  # type: ignore[arg-type]
    skill = SkillDefinition(
        name="Duas etapas",
        steps=(
            SkillStep("low_tool", {"value": "a"}),
            SkillStep("low_tool", {"value": "b"}),
        ),
    )

    result = runner.execute(skill)

    assert result.success is True
    assert executor.calls == [
        ("low_tool", {"value": "a"}),
        ("low_tool", {"value": "b"}),
    ]


def test_skill_executor_can_use_harness_execution_wrapper() -> None:
    registry = ToolRegistry()
    registry.register(LowTool())
    executor = RecordingExecutor()
    wrapped_calls: list[tuple[str, dict[str, str]]] = []

    def wrapped(tool, arguments):  # type: ignore[no-untyped-def]
        wrapped_calls.append((tool.name, dict(arguments)))
        return ToolExecutionResult("wrapped", "success", True)

    runner = SkillExecutor(
        registry, executor, execute_step=wrapped  # type: ignore[arg-type]
    )
    skill = SkillDefinition(
        name="Uma etapa", steps=(SkillStep("low_tool", {"value": "a"}),)
    )

    result = runner.execute(skill)

    assert result.success is True
    assert wrapped_calls == [("low_tool", {"value": "a"})]
    assert executor.calls == []
