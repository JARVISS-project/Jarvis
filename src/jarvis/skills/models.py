from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ToolTraceStep:
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class SkillStep:
    tool_name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SkillStep":
        name = value.get("tool_name")
        arguments = value.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise ValueError("Etapa de Skill inválida.")
        return cls(name, dict(arguments))


@dataclass
class SkillDefinition:
    name: str
    steps: tuple[SkillStep, ...]
    original_request: str = ""
    description: str = ""
    triggers: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    execution_count: int = 0
    success_count: int = 0
    last_used_at: str | None = None

    def mark_execution(self, success: bool) -> None:
        self.execution_count += 1
        if success:
            self.success_count += 1
        self.last_used_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "original_request": self.original_request,
            "triggers": list(self.triggers),
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at,
            "execution_count": self.execution_count,
            "success_count": self.success_count,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SkillDefinition":
        try:
            steps_raw = value["steps"]
            if not isinstance(steps_raw, list) or not steps_raw:
                raise ValueError("Skill sem etapas.")
            triggers_raw = value.get("triggers", [])
            if not isinstance(triggers_raw, list):
                raise ValueError("Triggers inválidos.")
            skill = cls(
                id=str(value["id"]),
                name=str(value["name"]),
                description=str(value.get("description", "")),
                original_request=str(value.get("original_request", "")),
                triggers=tuple(str(item) for item in triggers_raw),
                steps=tuple(SkillStep.from_dict(item) for item in steps_raw),
                created_at=str(value.get("created_at", "")),
                execution_count=int(value.get("execution_count", 0)),
                success_count=int(value.get("success_count", 0)),
                last_used_at=(
                    str(value["last_used_at"])
                    if value.get("last_used_at") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Skill persistida inválida.") from error
        if not skill.id.strip() or not skill.name.strip():
            raise ValueError("Skill persistida inválida.")
        return skill
