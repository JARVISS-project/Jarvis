from __future__ import annotations

import json
from pathlib import Path

from jarvis.orca_models import TaskDefinition, TaskRunState, TaskStatus, TERMINAL_STATUSES, _now


class TaskQueue:
    """Persistent dependency-aware queue for hands-off ORCA runs."""

    def __init__(self, backlog_path: Path, state_path: Path):
        self.backlog_path = Path(backlog_path)
        self.state_path = Path(state_path)
        self.tasks = self._load_tasks()
        self.state = self._load_state()
        self._ensure_state_rows()

    def _load_tasks(self) -> dict[str, TaskDefinition]:
        if not self.backlog_path.exists():
            return {}
        raw = json.loads(self.backlog_path.read_text(encoding="utf-8"))
        rows = raw.get("tasks", raw) if isinstance(raw, dict) else raw
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
            
        # Detect cycles
        for task_id in by_id:
            visited = set()
            stack = [task_id]
            while stack:
                curr = stack.pop()
                if curr in visited:
                    raise ValueError(f"cyclic dependency detected involving {curr}")
                visited.add(curr)
                stack.extend(by_id[curr].dependencies)
                
        return by_id

    def _load_state(self) -> dict[str, TaskRunState]:
        if not self.state_path.exists():
            return {}
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        rows = raw.get("tasks", raw) if isinstance(raw, dict) else raw
        return {row["task_id"]: TaskRunState.from_dict(row) for row in rows}

    def _ensure_state_rows(self) -> None:
        for task_id in self.tasks:
            if task_id not in self.state:
                self.state[task_id] = TaskRunState(task_id=task_id)

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
        import dataclasses
        data = dataclasses.asdict(state)
        data["status"] = state.status.value
        return data

    def dependency_satisfied(self, task: TaskDefinition) -> bool:
        return all(self.state[dep].status == TaskStatus.DONE for dep in task.dependencies)

    def dependency_failed_or_blocked(self, task: TaskDefinition) -> bool:
        return any(self.state[dep].status in {TaskStatus.FAILED, TaskStatus.BLOCKED} for dep in task.dependencies)

    def ready(self) -> list[TaskDefinition]:
        ready: list[TaskDefinition] = []
        for task in self.tasks.values():
            state = self.state[task.task_id]
            if not task.enabled or task.human_gate_required:
                continue
            if state.status in TERMINAL_STATUSES or state.status == TaskStatus.RUNNING:
                continue
            if self.dependency_failed_or_blocked(task):
                continue
            if self.dependency_satisfied(task):
                ready.append(task)
        return sorted(ready, key=lambda task: (task.priority, task.task_id))

    def refresh(self) -> None:
        for task in self.tasks.values():
            state = self.state[task.task_id]
            if state.status in TERMINAL_STATUSES or state.status == TaskStatus.RUNNING:
                continue
                
            if task.human_gate_required:
                target = TaskStatus.BLOCKED
                state.last_error = "human gate required"
            elif self.dependency_failed_or_blocked(task):
                target = TaskStatus.BLOCKED
                state.last_error = "dependency failed or blocked"
            elif task.enabled and self.dependency_satisfied(task):
                target = TaskStatus.READY
            else:
                target = TaskStatus.BACKLOG
                
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

    def acquire_lock(self, task_id: str, owner: str) -> bool:
        state = self.state[task_id]
        # Phase 23 - Task Locking
        if state.lock_owner and state.lock_owner != owner:
            return False
        state.lock_owner = owner
        state.lock_time = _now()
        self.save()
        return True

    def release_lock(self, task_id: str, owner: str) -> None:
        state = self.state.get(task_id)
        if state and state.lock_owner == owner:
            state.lock_owner = None
            state.lock_time = None
            self.save()

    def summary(self) -> list[dict]:
        self.refresh()
        rows = []
        for task in sorted(self.tasks.values(), key=lambda item: (item.priority, item.task_id)):
            state = self.state[task.task_id]
            rows.append(
                {
                    "task_id": task.task_id,
                    "issue": task.issue_number,
                    "title": task.title,
                    "status": state.status.value,
                    "attempts": state.attempts,
                    "dependencies": list(task.dependencies),
                    "human_gate_required": task.human_gate_required,
                }
            )
        return rows
