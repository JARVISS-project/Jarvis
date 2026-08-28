"""Structured results produced by tool execution."""

from dataclasses import dataclass
from typing import Literal


ToolExecutionStatus = Literal[
    "success",
    "denied",
    "security_blocked",
    "not_found",
    "error",
    "confirmation_required",
]
VisualChangeStatus = Literal["changed", "unchanged"]


@dataclass(frozen=True)
class ToolExecutionResult:
    """Internal execution metadata kept separate from user-facing content."""

    content: str
    status: ToolExecutionStatus
    authorized: bool
    visual_change: VisualChangeStatus | None = None
