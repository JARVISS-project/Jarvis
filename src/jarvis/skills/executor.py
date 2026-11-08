from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jarvis.skills.models import SkillDefinition
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.validator import ToolArgumentValidator


@dataclass(frozen=True)
class SkillExecutionResult:
    success: bool
    completed_steps: int
    results: tuple[ToolExecutionResult, ...]
    message: str


class SkillExecutor:
    """Executes recorded steps through the normal ToolExecutor/security path.

    ``execute_step`` lets the Harness wrap Skill replay with the same tracing,
    latency telemetry and debug output used by ordinary Tool calls. When no
    wrapper is supplied (for isolated unit use), execution still goes directly
    through the normal ToolExecutor and therefore keeps all security checks.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        execute_step: Callable[[Any, dict[str, Any]], ToolExecutionResult]
        | None = None,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.validator = ToolArgumentValidator()
        self.execute_step = execute_step

    def execute(self, skill: SkillDefinition) -> SkillExecutionResult:
        results: list[ToolExecutionResult] = []
        for index, step in enumerate(skill.steps, 1):
            tool = self.registry.get(step.tool_name)
            if tool is None:
                return SkillExecutionResult(
                    False,
                    index - 1,
                    tuple(results),
                    f"A Skill depende da Tool indisponível '{step.tool_name}'.",
                )
            valid, error = self.validator.validate(tool, step.arguments)
            if not valid:
                return SkillExecutionResult(
                    False,
                    index - 1,
                    tuple(results),
                    f"Etapa inválida da Skill: {error}",
                )
            arguments = dict(step.arguments)
            if self.execute_step is not None:
                result = self.execute_step(tool, arguments)
            else:
                # Same executor used by normal agent calls. MEDIUM/HIGH actions
                # still ask for confirmation; CRITICAL remains blocked.
                result = self.executor.execute_with_result(tool, **arguments)
            results.append(result)
            if result.status != "success":
                return SkillExecutionResult(
                    False,
                    index,
                    tuple(results),
                    result.content,
                )
        return SkillExecutionResult(
            True,
            len(skill.steps),
            tuple(results),
            "Skill executada com sucesso.",
        )
