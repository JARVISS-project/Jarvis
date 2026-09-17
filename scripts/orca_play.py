from __future__ import annotations

import argparse
from pathlib import Path

from jarvis.orca_tasks import TaskQueue
from jarvis.orca_worker import OpenCodeExecutor, OpenCodeExecutorConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the ORCA ready-task queue in hands-off mode."
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Isolated Jarvis-worktrees workspace used by the autonomous worker.",
    )
    parser.add_argument("--backlog", default="config/orca_tasks.json")
    parser.add_argument("--state", default=".orca/task_state.json")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--max-tasks", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    queue = TaskQueue(Path(args.backlog), Path(args.state))
    executor = OpenCodeExecutor(
        OpenCodeExecutorConfig(
            workspace=Path(args.workspace),
            timeout_seconds=args.timeout,
        )
    )

    completed = queue.run_ready(executor, max_tasks=args.max_tasks)
    if completed:
        print("Completed tasks:")
        for task_id in completed:
            print(f"- {task_id}")
    else:
        print("No task completed. Check queue state for BLOCKED/FAILED tasks or unmet dependencies.")

    print("\nQueue state:")
    for row in queue.summary():
        issue = f"#{row['issue']} " if row.get("issue") else ""
        print(f"{row['status']:<10} {row['task_id']:<24} {issue}{row['title']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
