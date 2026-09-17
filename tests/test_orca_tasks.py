import json
from pathlib import Path

from jarvis.orca_tasks import (
    TaskExecutionResult,
    TaskQueue,
    TaskStatus,
)


def _write_backlog(path: Path, tasks: list[dict]) -> None:
    path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")


def test_dependencies_gate_ready_tasks(tmp_path: Path) -> None:
    backlog = tmp_path / "tasks.json"
    state = tmp_path / "state.json"
    _write_backlog(
        backlog,
        [
            {
                "task_id": "a",
                "title": "A",
                "objective": "first",
                "priority": 10,
            },
            {
                "task_id": "b",
                "title": "B",
                "objective": "second",
                "dependencies": ["a"],
                "priority": 20,
            },
        ],
    )

    queue = TaskQueue(backlog, state)
    assert [task.task_id for task in queue.ready()] == ["a"]

    queue.set_status("a", TaskStatus.DONE)
    assert [task.task_id for task in queue.ready()] == ["b"]


def test_human_gate_is_not_auto_ready(tmp_path: Path) -> None:
    backlog = tmp_path / "tasks.json"
    state = tmp_path / "state.json"
    _write_backlog(
        backlog,
        [
            {
                "task_id": "payment",
                "title": "Payment",
                "objective": "requires human",
                "human_gate_required": True,
            }
        ],
    )

    queue = TaskQueue(backlog, state)
    assert queue.ready() == []


def test_run_ready_advances_to_next_dependency(tmp_path: Path) -> None:
    backlog = tmp_path / "tasks.json"
    state = tmp_path / "state.json"
    _write_backlog(
        backlog,
        [
            {"task_id": "a", "title": "A", "objective": "first", "priority": 10},
            {
                "task_id": "b",
                "title": "B",
                "objective": "second",
                "dependencies": ["a"],
                "priority": 20,
            },
        ],
    )

    seen: list[str] = []

    def executor(task, state):
        seen.append(task.task_id)
        return TaskExecutionResult(success=True, worker="opencode")

    queue = TaskQueue(backlog, state)
    completed = queue.run_ready(executor)

    assert completed == ["a", "b"]
    assert seen == ["a", "b"]
    assert queue.state["a"].status == TaskStatus.DONE
    assert queue.state["b"].status == TaskStatus.DONE


def test_failed_task_retries_without_human_prompt(tmp_path: Path) -> None:
    backlog = tmp_path / "tasks.json"
    state = tmp_path / "state.json"
    _write_backlog(
        backlog,
        [
            {
                "task_id": "retry",
                "title": "Retry",
                "objective": "retry automatically",
                "max_retries": 2,
            }
        ],
    )

    calls = 0

    def executor(task, state):
        nonlocal calls
        calls += 1
        return TaskExecutionResult(
            success=calls >= 2,
            worker="opencode",
            detail="synthetic failure" if calls < 2 else "",
        )

    queue = TaskQueue(backlog, state)
    completed = queue.run_ready(executor)

    assert completed == ["retry"]
    assert calls == 2
    assert queue.state["retry"].attempts == 2
    assert queue.state["retry"].status == TaskStatus.DONE


def test_executor_can_raise_real_human_gate(tmp_path: Path) -> None:
    backlog = tmp_path / "tasks.json"
    state = tmp_path / "state.json"
    _write_backlog(
        backlog,
        [{"task_id": "mfa", "title": "MFA", "objective": "stop for MFA"}],
    )

    def executor(task, state):
        return TaskExecutionResult(
            success=False,
            worker="opencode",
            detail="MFA required",
            needs_human=True,
        )

    queue = TaskQueue(backlog, state)
    queue.run_ready(executor)

    assert queue.state["mfa"].status == TaskStatus.BLOCKED
    assert queue.state["mfa"].last_error == "MFA required"


def test_state_is_persisted(tmp_path: Path) -> None:
    backlog = tmp_path / "tasks.json"
    state = tmp_path / "state.json"
    _write_backlog(
        backlog,
        [{"task_id": "a", "title": "A", "objective": "persist"}],
    )

    first = TaskQueue(backlog, state)
    first.set_status("a", TaskStatus.DONE)

    second = TaskQueue(backlog, state)
    assert second.state["a"].status == TaskStatus.DONE
