from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from jarvis.orca_models import TaskDefinition

class WorktreeManager:
    def __init__(self, main_repo_path: Path, worktrees_root: Path):
        self.main_repo = main_repo_path.resolve()
        self.worktrees_root = worktrees_root.resolve()
        self.worktrees_root.mkdir(parents=True, exist_ok=True)

    def _safe_slug(self, text: str) -> str:
        text = text.lower()
        text = re.sub(r'[^a-z0-9]+', '-', text)
        return text.strip('-')[:50]

    def _branch_name(self, task: TaskDefinition) -> str:
        issue = task.issue_number or 0
        slug = self._safe_slug(task.title)
        return f"orca/issue-{issue}-{slug}"

    def prepare_worktree(self, task: TaskDefinition) -> Path:
        branch = self._branch_name(task)
        wt_path = self.worktrees_root / task.task_id

        if wt_path.exists():
            return wt_path

        # Create branch if not exists
        check_branch = subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
            cwd=self.main_repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        if check_branch.returncode != 0:
            subprocess.run(
                ["git", "branch", branch],
                cwd=self.main_repo,
                check=True
            )

        # Create worktree
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), branch],
            cwd=self.main_repo,
            check=True
        )

        return wt_path

    def cleanup_worktree(self, task: TaskDefinition) -> None:
        wt_path = self.worktrees_root / task.task_id
        if wt_path.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=self.main_repo,
                check=True
            )
