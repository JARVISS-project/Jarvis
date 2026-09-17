from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Protocol


class TaskStatus(str, Enum):
    BACKLOG = "BACKLOG"
    READY = "READY"
    RUNNING = "RUNNING"
    TESTING = "TESTING"
    REVIEWING = "REVIEWING"
    FIXING = "FIXING"
    INTEGRATING = "INTEGRATING"
    MEMORY = "MEMORY"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    FAILED = "FAILED"


TERMINAL_STATUSES = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.BLOCKED}


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    title: str
    objective: str
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    priority: int = 100
    enabled: bool = True
    human_gate_required: bool = False
    max_retries: int = 3
    issue: int | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskDefinition":
        return cls(
            task_id=str(raw["task_id"]),
            title=str(raw["title"]),
            objective=str(raw["objective"]),
            dependencies=tuple(str(x) for x in raw.get("dependencies", [])),
            acceptance_criteria=tuple(str(x) for x in raw.get("acceptance_criteria", [])),
            priority=int(raw.get("priority", 100)),
            enabled=bool(raw.get("enabled", True)),
            human_gate_required=bool(raw.get("human_gate_required", False)),
            max_retries=max(0, int(raw.get("max_retries", 3))),
            issue=int(raw["issue"]) if raw.get("issue") is not None else None,
        )


@dataclass
class TaskRunState:
    task_id: str
    status: TaskStatus = TaskStatus.BACKLOG
    attempts: int = 0
    last_error: str | None = None
    worker: str | None = None
    started_at: str | None = None
    updated_at: str = field(default_factory=lambda: _now())
    completed_at: str | None = None

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
        )


@dataclass(frozen=True)
class TaskExecutionResult:
    success: bool
    worker: str
    detail: str = ""
    needs_human: bool = False


class TaskExecutor(Protocol):
    def __call__(self, task: TaskDefinition, state: TaskRunState) -> TaskExecutionResult: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskQueue:
    """Persistent dependency-aware queue for hands-off ORCA runs.

    The queue grants routine authority at Task scope. It does not weaken global
    policy and does not authorize destructive or out-of-scope operations.
    """

    def __init__(self, backlog_path: Path, state_path: Path):
        self.backlog_path = Path(backlog_path)
        self.state_path = Path(state_path)
        self.tasks = self._load_tasks()
        self.state = self._load_state()
        self._ensure_state_rows()

    def _load_tasks(self) -> dict[str, TaskDefinition]:
        raw = json.loads(self.backlog_path.read_text(encoding="utf-8"))
        rows = raw.get("tasks", raw)
        tasks = [TaskDefinition.from_dict(item) for item in rows]
        by_id = {task.task_id: task for task in tasks}
        if len(by_id) != len(tasks):
            raise ValueError("duplicate task_id in backlog")
        unknown = {
            dep
            for task in tasks
            for dep in task.dependencies
            if dep not in by_id
        }
        if unknown:
            raise ValueError(f"unknown task dependencies: {sorted(unknown)}")
        return by_id

    def _load_state(self) -> dict[str, TaskRunState]:
        if not self.state_path.exists():
            return {}
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        rows = raw.get("tasks", raw)
        return {row["task_id"]: TaskRunState.from_dict(row) for row in rows}

    def _ensure_state_rows(self) -> None:
        for task_id in self.tasks:
            self.state.setdefault(task_id, TaskRunState(task_id=task_id))

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now(),
            "tasks": [self._state_dict(self.state[task_id]) for task_id in sorted(self.state)],
        }
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(self.state_path)

    @staticmethod
    def _state_dict(state: TaskRunState) -> dict:
        data = asdict(state)
        data["status"] = state.status.value
        return data

    def dependency_satisfied(self, task: TaskDefinition) -> bool:
        return all(self.state[dep].status == TaskStatus.DONE for dep in task.dependencies)

    def ready(self) -> list[TaskDefinition]:
        ready: list[TaskDefinition] = []
        for task in self.tasks.values():
            state = self.state[task.task_id]
            if not task.enabled or task.human_gate_required:
                continue
            if state.status in TERMINAL_STATUSES or state.status == TaskStatus.RUNNING:
                continue
            if self.dependency_satisfied(task):
                ready.append(task)
        return sorted(ready, key=lambda task: (task.priority, task.task_id))

    def refresh(self) -> None:
        for task in self.tasks.values():
            state = self.state[task.task_id]
            if state.status in TERMINAL_STATUSES or state.status == TaskStatus.RUNNING:
                continue
            target = TaskStatus.READY if (
                task.enabled and not task.human_gate_required and self.dependency_satisfied(task)
            ) else TaskStatus.BACKLOG
            if state.status != target:
                state.status = target
                state.updated_at = _now()
        self.save()

    def set_status(self, task_id: str, status: TaskStatus, *, error: str | None = None) -> None:
        state = self.state[task_id]
        state.status = status
        state.updated_at = _now()
        state.last_error = error
        if status == TaskStatus.RUNNING and state.started_at is None:
            state.started_at = state.updated_at
        if status == TaskStatus.DONE:
            state.completed_at = state.updated_at
        self.save()

    def run_ready(self, executor: TaskExecutor, *, max_tasks: int | None = None) -> list[str]:
        completed: list[str] = []
        processed = 0
        while True:
            self.refresh()
            candidates = self.ready()
            if not candidates or (max_tasks is not None and processed >= max_tasks):
                break
            task = candidates[0]
            state = self.state[task.task_id]
            state.attempts += 1
            state.status = TaskStatus.RUNNING
            state.started_at = state.started_at or _now()
            state.updated_at = _now()
            state.last_error = None
            self.save()

            result = executor(task, state)
            state.worker = result.worker
            state.updated_at = _now()
            if result.needs_human:
                state.status = TaskStatus.BLOCKED
                state.last_error = result.detail or "human gate required"
            elif result.success:
                state.status = TaskStatus.DONE
                state.completed_at = state.updated_at
                state.last_error = None
                completed.append(task.task_id)
            elif state.attempts <= task.max_retries:
                state.status = TaskStatus.READY
                state.last_error = result.detail or "execution failed; retry queued"
            else:
                state.status = TaskStatus.FAILED
                state.last_error = result.detail or "retry budget exhausted"
            self.save()
            processed += 1
        return completed

    def summary(self) -> list[dict]:
        self.refresh()
        rows = []
        for task in sorted(self.tasks.values(), key=lambda item: (item.priority, item.task_id)):
            state = self.state[task.task_id]
            rows.append(
                {
                    "task_id": task.task_id,
                    "issue": task.issue,
                    "title": task.title,
                    "status": state.status.value,
                    "attempts": state.attempts,
                    "dependencies": list(task.dependencies),
                    "human_gate_required": task.human_gate_required,
                }
            )
        return rows


def _print_rows(rows: Iterable[dict]) -> None:
    for row in rows:
        issue = f"#{row['issue']} " if row.get("issue") else ""
        print(f"{row['status']:<10} {row['task_id']:<18} {issue}{row['title']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JARVIS/ORCA persistent task queue")
    parser.add_argument("--backlog", default="config/orca_tasks.json")
    parser.add_argument("--state", default=".orca/task_state.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show all tasks")
    sub.add_parser("ready", help="show tasks that can start now")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    queue = TaskQueue(Path(args.backlog), Path(args.state))
    if args.command == "list":
        _print_rows(queue.summary())
        return 0
    if args.command == "ready":
        queue.refresh()
        ready_ids = {task.task_id for task in queue.ready()}
        _print_rows(row for row in queue.summary() if row["task_id"] in ready_ids)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
