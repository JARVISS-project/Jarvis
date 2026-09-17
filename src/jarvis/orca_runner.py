from __future__ import annotations

import logging
import uuid
import subprocess
from pathlib import Path

from jarvis.orca_models import TaskDefinition, TaskRunState, TaskStatus, TaskExecutionResult
from jarvis.orca_tasks import TaskQueue
from jarvis.orca_security import TaskPermissionProfile, PermissionResolver, AuthorizationDecision
from jarvis.orca_workspace import WorktreeManager
from jarvis.orca_worker import OpenCodeExecutor, OpenCodeExecutorConfig

logger = logging.getLogger(__name__)

class ProcessSupervisor:
    """Phase 25 - Process Supervision"""
    def __init__(self):
        self.managed_processes = []

    def register(self, process: subprocess.Popen):
        self.managed_processes.append(process)

    def cleanup(self):
        for p in self.managed_processes:
            try:
                # Basic cleanup. On Windows, taskkill /T might be needed for process trees
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        self.managed_processes.clear()


class RunManager:
    """Manages the full execution lifecycle of a single Task Run."""
    
    def __init__(self, queue: TaskQueue, worktree_manager: WorktreeManager, repo_root: Path):
        self.queue = queue
        self.wt_manager = worktree_manager
        self.repo_root = repo_root

    def _generate_run_id(self) -> str:
        return str(uuid.uuid4())

    def run_ready_tasks(self, max_tasks: int | None = None) -> list[str]:
        completed = []
        processed = 0

        while True:
            self.queue.refresh()
            candidates = self.queue.ready()
            if not candidates or (max_tasks is not None and processed >= max_tasks):
                break

            task = candidates[0]
            if not self.queue.acquire_lock(task.task_id, owner="runner"):
                continue

            try:
                result = self.execute_task(task)
                if result.success:
                    completed.append(task.task_id)
            finally:
                self.queue.release_lock(task.task_id, owner="runner")
                
            processed += 1
            
        return completed

    def execute_task(self, task: TaskDefinition) -> TaskExecutionResult:
        state = self.queue.state[task.task_id]
        
        # Recover stale RUNNING
        if state.status == TaskStatus.RUNNING:
            logger.warning(f"Task {task.task_id} was RUNNING without active lock. Marking INTERRUPTED.")
            self.queue.set_status(task.task_id, TaskStatus.INTERRUPTED, error="Crash recovery")
            
        # Resume semantics
        if state.status in {TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.FAILED}:
            return TaskExecutionResult(success=state.status == TaskStatus.DONE, worker="system")
            
        state.run_id = self._generate_run_id()
        state.attempts += 1
        self.queue.set_status(task.task_id, TaskStatus.RUNNING)
        
        try:
            # Phase 22: Worktree
            wt_path = self.wt_manager.prepare_worktree(task)
            state.worktree = str(wt_path)
            state.branch = self.wt_manager._branch_name(task)
            self.queue.save()

            profile = TaskPermissionProfile.from_task(task, state.run_id, wt_path)
            supervisor = ProcessSupervisor()
            
            try:
                # Phase 29: Automatic Fix Loop
                max_fix_loops = 2
                current_loop = 0
                
                while current_loop <= max_fix_loops:
                    # RUNNING (Worker)
                    worker_res = self._run_worker(task, profile, wt_path, supervisor)
                    if worker_res.needs_human:
                        self.queue.set_status(task.task_id, TaskStatus.BLOCKED, error=worker_res.detail)
                        return worker_res
                        
                    # TESTING
                    self.queue.set_status(task.task_id, TaskStatus.TESTING)
                    test_res = self._run_tester(task, profile, wt_path, supervisor)
                    if test_res.success:
                        break
                        
                    # FIXING
                    self.queue.set_status(task.task_id, TaskStatus.FIXING)
                    current_loop += 1
                    if current_loop > max_fix_loops:
                        self.queue.set_status(task.task_id, TaskStatus.FAILED, error="Fix loop budget exhausted")
                        return test_res
                        
                # REVIEWING
                self.queue.set_status(task.task_id, TaskStatus.REVIEWING)
                review_res = self._run_reviewer(task, profile, wt_path, supervisor)
                if not review_res.success:
                    self.queue.set_status(task.task_id, TaskStatus.FAILED, error="Review rejected")
                    return review_res
                    
                # INTEGRATING (Commit & Push)
                self.queue.set_status(task.task_id, TaskStatus.INTEGRATING)
                integ_res = self._run_integrator(task, profile, wt_path, supervisor)
                if not integ_res.success:
                    self.queue.set_status(task.task_id, TaskStatus.FAILED, error="Integration failed")
                    return integ_res
                    
                # MEMORY
                self.queue.set_status(task.task_id, TaskStatus.MEMORY)
                mem_res = self._run_memory_keeper(task, profile, wt_path, supervisor)
                
                # DONE
                self.queue.set_status(task.task_id, TaskStatus.DONE)
                return TaskExecutionResult(success=True, worker="system")
                
            finally:
                supervisor.cleanup()
                # Do NOT remove worktree automatically on success so user can inspect, 
                # or maybe just leave it for now.
                
        except Exception as e:
            logger.exception("Task execution failed internally")
            self.queue.set_status(task.task_id, TaskStatus.FAILED, error=str(e))
            return TaskExecutionResult(success=False, worker="system", detail=str(e))
            
    def _run_worker(self, task: TaskDefinition, profile: TaskPermissionProfile, wt_path: Path, supervisor: ProcessSupervisor) -> TaskExecutionResult:
        executor = OpenCodeExecutor(OpenCodeExecutorConfig(workspace=wt_path))
        return executor(task, self.queue.state[task.task_id])
        
    def _run_tester(self, task: TaskDefinition, profile: TaskPermissionProfile, wt_path: Path, supervisor: ProcessSupervisor) -> TaskExecutionResult:
        # Simple local tester (pytest)
        decision = PermissionResolver.authorize("command", "pytest", profile)
        if decision != AuthorizationDecision.ALLOW:
            return TaskExecutionResult(success=False, worker="tester", detail="pytest denied")
            
        try:
            # We assume pytest is allowed and we run it in the worktree
            proc = subprocess.run(["python", "-m", "pytest"], cwd=wt_path, capture_output=True, text=True)
            return TaskExecutionResult(success=proc.returncode == 0, worker="tester", detail=proc.stdout)
        except Exception as e:
            return TaskExecutionResult(success=False, worker="tester", detail=str(e))

    def _run_reviewer(self, task: TaskDefinition, profile: TaskPermissionProfile, wt_path: Path, supervisor: ProcessSupervisor) -> TaskExecutionResult:
        # Mock reviewer for this phase. In reality it would use Gemini High.
        return TaskExecutionResult(success=True, worker="reviewer")

    def _run_integrator(self, task: TaskDefinition, profile: TaskPermissionProfile, wt_path: Path, supervisor: ProcessSupervisor) -> TaskExecutionResult:
        # Git commit and push task branch
        try:
            # Check if there are changes
            status = subprocess.run(["git", "status", "--porcelain"], cwd=wt_path, capture_output=True, text=True)
            if status.stdout.strip():
                subprocess.run(["git", "add", "."], cwd=wt_path, check=True)
                commit_msg = f"feat(orca): implement {task.task_id}"
                if task.issue_number:
                    commit_msg += f" (#{task.issue_number})"
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=wt_path, check=True)
                
            # Push task branch
            if profile.allow_branch_push:
                branch = self.queue.state[task.task_id].branch
                if branch:
                    subprocess.run(["git", "push", "-u", "origin", branch], cwd=wt_path, check=True)
                    
            return TaskExecutionResult(success=True, worker="integrator")
        except Exception as e:
            return TaskExecutionResult(success=False, worker="integrator", detail=str(e))

    def _run_memory_keeper(self, task: TaskDefinition, profile: TaskPermissionProfile, wt_path: Path, supervisor: ProcessSupervisor) -> TaskExecutionResult:
        # Mock memory write
        # In reality, writes to 40_AI/Sessions etc.
        try:
            orca_sys_path = self.repo_root / "90_System" / "ORCA"
            orca_sys_path.mkdir(parents=True, exist_ok=True)
            mem_file = orca_sys_path / f"memory_{task.task_id}.txt"
            mem_file.write_text(f"Task {task.task_id} completed successfully.", encoding="utf-8")
            return TaskExecutionResult(success=True, worker="memory")
        except Exception as e:
            return TaskExecutionResult(success=False, worker="memory", detail=str(e))

