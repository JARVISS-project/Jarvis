from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class TaskStatus(str, Enum):
    BACKLOG = "BACKLOG"
    READY = "READY"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    TESTING = "TESTING"
    REVIEWING = "REVIEWING"
    FIXING = "FIXING"
    INTEGRATING = "INTEGRATING"
    MEMORY = "MEMORY"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    DONE = "DONE"


TERMINAL_STATUSES = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.BLOCKED}


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    title: str
    objective: str
    issue_number: int | None = None
    priority: int = 100
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    
    # Permissions
    allowed_read_paths: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    allowed_git_operations: tuple[str, ...] = ()
    prohibited_operations: tuple[str, ...] = ()
    
    # Execution
    preferred_worker: str = "opencode"
    max_retries: int = 3
    enabled: bool = True
    human_gate_required: bool = False
    
    # State tracking
    github_state: str = "open"
    local_state: str = "BACKLOG"
    labels: tuple[str, ...] = ()
    
    # Workspace
    branch: str | None = None
    worktree: str | None = None
    
    metadata_version: int = 1

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskDefinition":
        return cls(
            task_id=str(raw["task_id"]),
            title=str(raw["title"]),
            objective=str(raw["objective"]),
            issue_number=int(raw["issue_number"]) if raw.get("issue_number") is not None else (int(raw["issue"]) if raw.get("issue") is not None else None),
            priority=int(raw.get("priority", 100)),
            dependencies=tuple(str(x) for x in raw.get("dependencies", [])),
            acceptance_criteria=tuple(str(x) for x in raw.get("acceptance_criteria", [])),
            
            allowed_read_paths=tuple(str(x) for x in raw.get("allowed_read_paths", [])),
            allowed_write_paths=tuple(str(x) for x in raw.get("allowed_write_paths", [])),
            allowed_commands=tuple(str(x) for x in raw.get("allowed_commands", [])),
            allowed_git_operations=tuple(str(x) for x in raw.get("allowed_git_operations", [])),
            prohibited_operations=tuple(str(x) for x in raw.get("prohibited_operations", [])),
            
            preferred_worker=str(raw.get("preferred_worker", "opencode")),
            max_retries=max(0, int(raw.get("max_retries", 3))),
            enabled=bool(raw.get("enabled", True)),
            human_gate_required=bool(raw.get("human_gate_required", False)),
            
            github_state=str(raw.get("github_state", "open")),
            local_state=str(raw.get("local_state", "BACKLOG")),
            labels=tuple(str(x) for x in raw.get("labels", [])),
            
            branch=raw.get("branch"),
            worktree=raw.get("worktree"),
            metadata_version=int(raw.get("metadata_version", 1)),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskRunState:
    task_id: str
    status: TaskStatus = TaskStatus.BACKLOG
    attempts: int = 0
    last_error: str | None = None
    worker: str | None = None
    started_at: str | None = None
    updated_at: str = field(default_factory=_now)
    completed_at: str | None = None
    
    run_id: str | None = None
    branch: str | None = None
    worktree: str | None = None
    lock_owner: str | None = None
    lock_time: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskRunState":
        return cls(
            task_id=str(raw["task_id"]),
            status=TaskStatus(raw.get("status", TaskStatus.BACKLOG.value)),
            attempts=int(raw.get("attempts", 0)),
            last_error=raw.get("last_error"),
            worker=raw.get("worker"),
            started_at=raw.get("started_at"),
            updated_at=raw.get("updated_at") or _now(),
            completed_at=raw.get("completed_at"),
            run_id=raw.get("run_id"),
            branch=raw.get("branch"),
            worktree=raw.get("worktree"),
            lock_owner=raw.get("lock_owner"),
            lock_time=raw.get("lock_time"),
        )

@dataclass(frozen=True)
class TaskExecutionResult:
    success: bool
    worker: str
    detail: str = ""
    needs_human: bool = False
    next_state: TaskStatus | None = None
