from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from jarvis.orca_models import TaskDefinition

logger = logging.getLogger(__name__)

class AuthorizationDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HUMAN_GATE = "HUMAN_GATE"

@dataclass(frozen=True)
class TaskPermissionProfile:
    task_id: str
    run_id: str
    workspace_root: Path
    allowed_read_paths: tuple[str, ...]
    allowed_write_paths: tuple[str, ...]
    allowed_commands: tuple[str, ...]
    allowed_git_operations: tuple[str, ...]
    blocked_operations: tuple[str, ...]
    human_gates: tuple[str, ...]
    allow_retry: bool
    allow_test: bool
    allow_review: bool
    allow_integration: bool
    allow_memory_update: bool
    allow_next_task: bool
    allow_branch_push: bool

    @classmethod
    def from_task(cls, task: TaskDefinition, run_id: str, workspace_root: Path) -> "TaskPermissionProfile":
        # Phase 7: Default Routine Permissions
        default_read = ("*",)  # inside authorized workspace/worktree
        default_write = ("*",) # inside authorized workspace/worktree
        
        default_commands = (
            "python", "pytest", "lint", "formatter", "typecheck", "build",
            "file search", "grep", "ls", "cat", "echo"
        )
        
        default_git = (
            "status", "diff", "log", "add", "commit", "branch", "worktree", "fetch", "push"
        )
        
        # Merge task allowed with default routine permissions
        allowed_reads = tuple(set(default_read + tuple(task.allowed_read_paths)))
        allowed_writes = tuple(set(default_write + tuple(task.allowed_write_paths)))
        allowed_cmds = tuple(set(default_commands + tuple(task.allowed_commands)))
        allowed_git = tuple(set(default_git + tuple(task.allowed_git_operations)))
        
        # Globally denied operations
        blocked = (
            "git reset", "git clean", "git rebase", "force push",
            "--ignore-rules", "policy bypass", "base64",
        ) + tuple(task.prohibited_operations)

        return cls(
            task_id=task.task_id,
            run_id=run_id,
            workspace_root=workspace_root.resolve(),
            allowed_read_paths=allowed_reads,
            allowed_write_paths=allowed_writes,
            allowed_commands=allowed_cmds,
            allowed_git_operations=allowed_git,
            blocked_operations=blocked,
            human_gates=("mfa", "new_secret", "payment", "credential_store"),
            allow_retry=True,
            allow_test=True,
            allow_review=True,
            allow_integration=True,
            allow_memory_update=True,
            allow_next_task=True,
            allow_branch_push=True,
        )


class PermissionResolver:
    @staticmethod
    def authorize(action: str, target: str, profile: TaskPermissionProfile) -> AuthorizationDecision:
        decision = PermissionResolver._evaluate(action, target, profile)
        logger.info(f"AUTHORIZATION: action={action} target={target} decision={decision.value} task_id={profile.task_id} run_id={profile.run_id}")
        return decision

    @staticmethod
    def _evaluate(action: str, target: str, profile: TaskPermissionProfile) -> AuthorizationDecision:
        action_lower = action.lower()
        target_lower = target.lower()

        # Human gates
        if action_lower in ["credential_store_access", "payment", "mfa"]:
            return AuthorizationDecision.HUMAN_GATE

        # Explicit blocked
        for blocked in profile.blocked_operations:
            if blocked in action_lower or blocked in target_lower:
                return AuthorizationDecision.DENY
                
        # Phase 8: Filesystem boundaries
        if action_lower == "write":
            try:
                target_path = Path(target).resolve()
            except Exception:
                return AuthorizationDecision.DENY

            # Outside workspace
            if not str(target_path).startswith(str(profile.workspace_root)):
                # Phase 8: Allowed operational Second Brain locations
                allowed_external = [
                    "40_ai\\sessions",
                    "40_ai\\handoffs",
                    "40_ai\\reviews",
                    "90_system\\orca"
                ]
                is_allowed_external = any(ext in str(target_path).lower() for ext in allowed_external)
                if not is_allowed_external:
                    return AuthorizationDecision.DENY

                # Explicitly deny 10_Pessoal, 30_JARVIS/Memoria, JarvisVault, secrets
                denied_external = [
                    "10_pessoal", "30_jarvis\\memoria", "jarvisvault", "secrets"
                ]
                if any(denied in str(target_path).lower() for denied in denied_external):
                    return AuthorizationDecision.DENY

        if action_lower == "read":
            pass # Routine reads are allowed in workspace, and main repository is allowed

        if action_lower == "command":
            if "git " in target_lower:
                if "push main" in target_lower:
                    return AuthorizationDecision.DENY
                if "reset" in target_lower or "rebase" in target_lower or "clean" in target_lower:
                    return AuthorizationDecision.DENY
                if "push" in target_lower and "force" in target_lower:
                    return AuthorizationDecision.DENY
                
            if "ignore-rules" in target_lower:
                return AuthorizationDecision.DENY

            # Allow safe
            return AuthorizationDecision.ALLOW

        if action_lower in ["git", "git_operation"]:
            if any(bad in target_lower for bad in ["reset", "clean", "rebase"]):
                return AuthorizationDecision.DENY
            if "push" in target_lower and "force" in target_lower:
                return AuthorizationDecision.DENY
            if "push main" in target_lower:
                return AuthorizationDecision.DENY

            return AuthorizationDecision.ALLOW
            
        return AuthorizationDecision.ALLOW
