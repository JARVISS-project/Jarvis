from pathlib import Path
from typing import Any

import pytest

from jarvis.security.audit import AuditLogger
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.validator import ToolArgumentValidator


class ProbeTool(Tool):
    name = "argument_probe"
    description = "Validação sem efeito externo"
    risk = RiskLevel.READ_ONLY
    parameters = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "enabled": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["safe"]},
        },
        "required": ["count"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.prepared = False
        self.executed = False

    def get_target(self, **kwargs: Any) -> str:
        self.prepared = True
        return "probe"

    def execute(self, **kwargs: Any) -> str:
        self.executed = True
        return "ok"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"count": True},
        {"count": "1"},
        {"count": 1, "enabled": 1},
        {"count": 1, "mode": "unsafe"},
        {"count": 1, "unknown": "x"},
    ],
)
def test_invalid_arguments_are_rejected_before_preparation(
    tmp_path: Path, arguments: dict[str, Any]
) -> None:
    tool = ProbeTool()
    valid, _ = ToolArgumentValidator().validate(tool, arguments)
    assert not valid
    result = ToolExecutor(SafetyManager(), AuditLogger(tmp_path / "audit.log")).execute_with_result(
        tool, **arguments
    )
    assert result.status == "error"
    assert not tool.prepared
    assert not tool.executed
