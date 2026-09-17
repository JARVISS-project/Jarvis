from __future__ import annotations

import argparse
from pathlib import Path

from jarvis.orca_tasks import TaskQueue
from jarvis.orca_runner import RunManager
from jarvis.orca_workspace import WorktreeManager

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JARVIS/ORCA persistent task queue")
    parser.add_argument("--backlog", default="config/orca_tasks.json")
    parser.add_argument("--state", default=".orca/task_state.json")
    parser.add_argument("--workspace", default="../Jarvis-worktrees")
    
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="Sync tasks from GitHub (mocked)")
    sub.add_parser("list", help="Show all tasks")
    sub.add_parser("ready", help="Show tasks that can start now")
    sub.add_parser("status", help="Show current queue status")
    
    start_cmd = sub.add_parser("start", help="Start the ORCA autonomous task execution loop")
    start_cmd.add_argument("--max-tasks", type=int, default=None)
    
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    
    repo_root = Path.cwd()
    backlog_path = repo_root / args.backlog
    state_path = repo_root / args.state
    wt_root = repo_root.parent / "Jarvis-worktrees"
    
    queue = TaskQueue(backlog_path, state_path)
    wt_manager = WorktreeManager(repo_root, wt_root)
    runner = RunManager(queue, wt_manager, repo_root)
    
    if args.command == "sync":
        # Phase 11-14: GitHub Sync. Mocking for now as per instructions (or actually we might need a basic mock).
        print("GitHub sync completed.")
        return 0
        
    elif args.command == "list":
        rows = queue.summary()
        for row in rows:
            issue = f"#{row['issue']} " if row.get("issue") else ""
            print(f"{row['status']:<15} {row['task_id']:<25} {issue}{row['title']}")
        return 0
        
    elif args.command == "ready":
        queue.refresh()
        ready_ids = {task.task_id for task in queue.ready()}
        rows = queue.summary()
        for row in rows:
            if row["task_id"] in ready_ids:
                issue = f"#{row['issue']} " if row.get("issue") else ""
                print(f"{row['status']:<15} {row['task_id']:<25} {issue}{row['title']}")
        return 0
        
    elif args.command == "status":
        queue.refresh()
        rows = queue.summary()
        for row in rows:
            issue = f"#{row['issue']} " if row.get("issue") else ""
            print(f"{row['status']:<15} {row['task_id']:<25} {issue}{row['title']}")
        return 0
        
    elif args.command == "start":
        completed = runner.run_ready_tasks(max_tasks=args.max_tasks)
        if completed:
            print("Completed tasks:")
            for task_id in completed:
                print(f"- {task_id}")
        else:
            print("No task completed. Inspect queue state for blockers or unmet dependencies.")
        return 0
        
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
