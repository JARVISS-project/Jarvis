from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jarvis.orca_tasks import TaskDefinition, TaskExecutionResult, TaskRunState


@dataclass(frozen=True)
class OpenCodeExecutorConfig:
    workspace: Path
    command: str = "opencode"
    timeout_seconds: int = 3600


class OpenCodeExecutor:
    """Run one ORCA task through OpenCode in a bounded, non-interactive process.

    This adapter intentionally refuses to operate directly in the canonical
    Jarvis working tree. Routine autonomy is granted only inside an isolated
    Jarvis-worktrees workspace.
    """

    def __init__(self, config: OpenCodeExecutorConfig):
        self.config = config
        self.workspace = config.workspace.resolve()
        self._validate_workspace()

    def _validate_workspace(self) -> None:
        normalized = str(self.workspace).replace("/", "\\").lower()
        if "jarvis-worktrees\\" not in normalized:
            raise ValueError(
                "OpenCodeExecutor requires an isolated Jarvis-worktrees workspace"
            )
        if not self.workspace.exists():
            raise FileNotFoundError(self.workspace)

    def __call__(self, task: TaskDefinition, state: TaskRunState) -> TaskExecutionResult:
        prompt = self._build_prompt(task)
        command = [self.config.command, "run", prompt, "--auto"]
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.config.timeout_seconds,
                shell=False,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            return TaskExecutionResult(
                success=False,
                worker="opencode",
                detail=f"OpenCode timed out after {self.config.timeout_seconds}s",
            )
        except OSError as exc:
            return TaskExecutionResult(
                success=False,
                worker="opencode",
                detail=f"OpenCode launch failed: {exc}",
            )

        detail = (completed.stdout or "").strip()
        if completed.stderr:
            stderr = completed.stderr.strip()
            detail = f"{detail}\nSTDERR: {stderr}".strip()

        return TaskExecutionResult(
            success=completed.returncode == 0,
            worker="opencode",
            detail=detail[-8000:],
        )

    @staticmethod
    def _build_prompt(task: TaskDefinition) -> str:
        criteria = "\n".join(f"- {item}" for item in task.acceptance_criteria) or "- Complete the objective safely."
        return f"""ROLE: ORCA AUTONOMOUS WORKER
TASK_ID: {task.task_id}
TITLE: {task.title}
OBJECTIVE: {task.objective}

ACCEPTANCE CRITERIA:
{criteria}

EXECUTION AUTHORITY:
- Routine read/edit/test/build/retry operations inside this worktree are pre-authorized.
- Do not ask for routine approval.
- Do not use base64, obfuscation, --ignore-rules, or equivalent policy bypasses.
- Do not access or mutate JarvisVault, personal memory, credentials, unrelated files, or the canonical main working tree.
- Do not run destructive Git commands or git push.
- Run relevant tests before declaring success.
- If a real human gate is required (MFA, new secret, payment, irreversible external effect, destructive Git, security-boundary reduction, or an out-of-scope decision), stop and report the blocker instead of bypassing it.

Return a concise result containing files changed, tests run, outcome, and any blocker.
"""
