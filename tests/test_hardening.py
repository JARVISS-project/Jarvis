from pathlib import Path
from typing import Any

import pytest

from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.authorization import AuthorizationGrantStore
from jarvis.security.policy import SecurityPolicy
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry


class ScriptedLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        return self.responses.pop(0)


class RecordingExecutor:
    def __init__(self, result: ToolExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_with_result(
        self,
        tool: Any,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        filtered = {k: v for k, v in kwargs.items() if k != "_interaction_id"}
        self.calls.append((tool.name, filtered))
        return self.result


class RecordingSafetyManager:
    def __init__(self, authorized: bool) -> None:
        self.authorized = authorized
        self.calls: list[tuple[str, str, Any]] = []
        self.grants = AuthorizationGrantStore()
        self.policy = SecurityPolicy()

    def authorize(self, action: str, target: str, risk: Any) -> bool:
        self.calls.append((action, target, risk))
        return self.authorized

    def authorize_invocation(self, action: str, target: str, risk: Any):
        self.calls.append((action, target, risk))
        return self.grants.issue(action, target) if self.authorized else None

    def consume_grant(self, grant, action: str, target: str) -> bool:
        return self.grants.consume(grant, action, target)

    def invalidate_session(self, session_id: str) -> None:
        pass


class RecordingAuditLogger:
    def log(self, **kwargs: Any) -> None:
        pass


class SpyDeleteFileTool(DeleteFileTool):
    def __init__(self, filesystem: FileSystemGuard) -> None:
        super().__init__(filesystem)
        self.execute_calls = 0

    def execute(self, **kwargs: Any) -> str:
        self.execute_calls += 1
        return super().execute(**kwargs)


def make_delete_harness(
    tmp_path: Path,
    llm: ScriptedLLM,
    executor: RecordingExecutor,
) -> Harness:
    registry = ToolRegistry()
    registry.register(DeleteFileTool(FileSystemGuard(tmp_path)))
    return Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=executor,  # type: ignore[arg-type]
        memory=ConversationMemory(),
    )


def delete_call(path: str) -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[
            ToolCallRequest(name="delete_file", arguments={"path": path})
        ],
    )


def test_ambiguous_delete_never_reaches_executor(tmp_path: Path) -> None:
    llm = ScriptedLLM([delete_call("README.md")])
    executor = RecordingExecutor(ToolExecutionResult("ok", "success", True))
    harness = make_delete_harness(tmp_path, llm, executor)

    result = harness.processar_mensagem("apague algum arquivo")

    assert result == "Qual arquivo você deseja apagar?"
    assert executor.calls == []


def test_explicit_delete_can_reach_confirmation_path(tmp_path: Path) -> None:
    llm = ScriptedLLM([delete_call("README.md")])
    executor = RecordingExecutor(ToolExecutionResult("negado", "denied", False))
    harness = make_delete_harness(tmp_path, llm, executor)

    result = harness.processar_mensagem("apague README.md")

    assert executor.calls == [("delete_file", {"path": "README.md"})]
    assert result == "A exclusão foi cancelada."


def test_denied_high_tool_stops_second_destructive_attempt(
    tmp_path: Path,
) -> None:
    llm = ScriptedLLM(
        [delete_call("AGENTS.md"), delete_call("README.md")]
    )
    executor = RecordingExecutor(ToolExecutionResult("negado", "denied", False))
    harness = make_delete_harness(tmp_path, llm, executor)

    result = harness.processar_mensagem(
        "apague AGENTS.md; se não der, apague README.md"
    )

    assert result == "A exclusão foi cancelada."
    assert llm.calls == 1
    assert executor.calls == [("delete_file", {"path": "AGENTS.md"})]


def test_agents_file_is_write_protected(tmp_path: Path) -> None:
    guard = FileSystemGuard(tmp_path)

    assert guard.resolve("AGENTS.md").name == "AGENTS.md"
    with pytest.raises(PermissionError):
        guard.resolve("AGENTS.md", for_write=True)


def make_preflight_harness(
    tmp_path: Path,
    llm: ScriptedLLM,
    authorized: bool,
) -> tuple[Harness, SpyDeleteFileTool, RecordingSafetyManager]:
    tool = SpyDeleteFileTool(FileSystemGuard(tmp_path))
    registry = ToolRegistry()
    registry.register(tool)
    safety = RecordingSafetyManager(authorized)
    executor = ToolExecutor(
        safety_manager=safety,  # type: ignore[arg-type]
        audit_logger=RecordingAuditLogger(),  # type: ignore[arg-type]
    )
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=executor,
        memory=ConversationMemory(),
    )
    return harness, tool, safety


def test_missing_delete_target_skips_confirmation_and_execution(
    tmp_path: Path,
) -> None:
    llm = ScriptedLLM([delete_call("ausente.txt")])
    harness, tool, safety = make_preflight_harness(tmp_path, llm, True)

    result = harness.processar_mensagem("apague ausente.txt")

    assert result == "Arquivo não encontrado."
    assert safety.calls == []
    assert tool.execute_calls == 0


def test_similar_filename_is_not_selected_automatically(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("preservado", encoding="utf-8")
    llm = ScriptedLLM(
        [delete_call("headme.md"), delete_call("README.md")]
    )
    harness, tool, safety = make_preflight_harness(tmp_path, llm, True)

    result = harness.processar_mensagem("apague headme.md")

    assert result == "Arquivo não encontrado."
    assert llm.calls == 1
    assert safety.calls == []
    assert tool.execute_calls == 0
    assert (tmp_path / "README.md").exists()


def test_existing_explicit_target_reaches_confirmation_and_denial_stops(
    tmp_path: Path,
) -> None:
    target = tmp_path / "README.md"
    target.write_text("preservado", encoding="utf-8")
    llm = ScriptedLLM(
        [delete_call("README.md"), delete_call("outro.txt")]
    )
    harness, tool, safety = make_preflight_harness(tmp_path, llm, False)

    result = harness.processar_mensagem("apague README.md")

    assert "Autoriza" in result
    context = harness.current_confirmation_context()
    result = harness.processar_mensagem("cancelar", confirmation_session_id=context[0], confirmation_challenge_id=context[1])
    assert "cancelada" in result
    assert tool.execute_calls == 0
    assert target.exists()
