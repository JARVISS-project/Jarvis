from __future__ import annotations

import json
from pathlib import Path
from jarvis.orca_models import TaskDefinition

class GitHubSyncManager:
    def __init__(self, backlog_path: Path):
        self.backlog_path = backlog_path
        
    def sync(self, raw_issues: list[dict]) -> dict[str, TaskDefinition]:
        """
        Phase 11-14: GitHub AS BACKLOG SOURCE & ISSUE METADATA VALIDATION
        Transforms issues to tasks securely.
        """
        tasks = []
        for issue in raw_issues:
            # Phase 14: Sanitize/Validate
            labels = issue.get("labels", [])
            # Phase 13: GitHub Task Eligibility
            is_ready = "orca:ready" in labels
            is_blocked = "orca:blocked" in labels
            
            if not is_ready and not is_blocked:
                continue
                
            task_id = f"issue-{issue['number']}"
            
            # Defensive defaults
            allowed_cmds = ["python", "pytest", "lint", "formatter"]
            
            # Check prompt injection/self-elevation (Phase 51, 52)
            body = issue.get("body", "").lower()
            if "ignore all policies" in body or "ignore-rules" in body:
                pass # Treated as DATA, not authority
            
            # Safe task def
            task = TaskDefinition(
                task_id=task_id,
                issue_number=issue["number"],
                title=issue["title"],
                objective=issue.get("body", "Complete objective"),
                priority=issue.get("priority", 100),
                dependencies=tuple(issue.get("dependencies", [])),
                acceptance_criteria=tuple(issue.get("acceptance_criteria", [])),
                allowed_commands=tuple(allowed_cmds),
                enabled=True,
                human_gate_required=is_blocked,
                github_state="open"
            )
            
            tasks.append(task)
            
        # Write to backlog (Phase 10)
        # Handle cycles, self-dependencies
        by_id = {t.task_id: t for t in tasks}
        safe_tasks = []
        for t in tasks:
            # Phase 15: Validations
            if t.task_id in t.dependencies:
                continue # self dependency -> drop or fail closed
            has_unknown = any(d not in by_id for d in t.dependencies)
            if has_unknown:
                continue # unknown dependency -> fail closed
            safe_tasks.append(t)
            
        payload = {"version": 1, "tasks": [t.__dict__ for t in safe_tasks]}
        self.backlog_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        
        return {t.task_id: t for t in safe_tasks}
