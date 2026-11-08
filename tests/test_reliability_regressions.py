from pathlib import Path

from jarvis.config import JarvisConfig
from jarvis.context.recent import RecentContext
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.security.audit import AuditLogger
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.get_runtime_info import GetRuntimeInfoTool
from jarvis.tools.registry import ToolRegistry


class NeverLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("LLM não deveria ser chamado")


class LeakingLLM(LLMClient):
    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        return LLMResponse(
            "Wait, no...\nThe assistant should use tool_call.\n"
            "</think>\nResposta correta."
        )


class SearchNotes(Tool):
    name = "obsidian_search_notes"
    description = "busca notas"
    risk = RiskLevel.READ_ONLY
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.queries: list[str] = []

    def get_target(self, **kwargs):  # type: ignore[no-untyped-def]
        return "Vault"

    def execute(self, **kwargs):  # type: ignore[no-untyped-def]
        query = kwargs["query"]
        self.queries.append(query)
        if query == "teste":
            return "- Teste.md | score=2 | resultado"
        return "Nenhuma nota encontrada."


class SearchLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return LLMResponse(
            "", [ToolCallRequest("obsidian_search_notes", {"query": "TXT"})]
        )


def executor(tmp_path: Path) -> ToolExecutor:
    return ToolExecutor(
        SafetyManager(), AuditLogger(tmp_path / "audit.log")
    )


def test_final_barrier_protects_return_and_memory(tmp_path: Path) -> None:
    memory = ConversationMemory()

    harness = Harness(
        LeakingLLM(), ToolRegistry(), executor(tmp_path), memory
    )
    result = harness.processar_mensagem("pergunta")
    assert result == "Resposta correta."
    assert memory.get_history()[-1]["content"] == "Resposta correta."
    assert "Wait, no" not in str(memory.get_history())
    assert "tool_call" not in str(memory.get_history())


def test_runtime_question_bypasses_llm_and_uses_real_values(tmp_path: Path) -> None:
    llm = NeverLLM()
    config = JarvisConfig(conversation_question_timeout_seconds=17)
    manager = ResourceManager()
    registry = ToolRegistry()
    registry.register(GetRuntimeInfoTool(config, manager))

    harness = Harness(
        llm, registry, executor(tmp_path), ConversationMemory(),
        config=config, resource_manager=manager,
    )
    result = harness.processar_mensagem("Qual é seu timeout?")
    assert "Pergunta: 17s" in result
    assert llm.calls == 0


def test_txt_search_suggests_recent_test_and_confirmation_retries(
    tmp_path: Path,
) -> None:
    llm = SearchLLM()
    tool = SearchNotes()
    registry = ToolRegistry()
    registry.register(tool)
    recent = RecentContext()
    recent.set(
        "last_note_results",
        ["Teste.md", "Teste para Jarvis.md", "teste 2.md"],
    )

    harness = Harness(
        llm, registry, executor(tmp_path), ConversationMemory(),
        recent_context=recent,
    )
    assert harness.processar_mensagem("Procure TXT") == "Você quis dizer 'teste'?"
    assert tool.queries == ["TXT"]
    result = harness.processar_mensagem("sim")
    assert "Teste.md" in result
    assert tool.queries == ["TXT", "teste"]
    assert llm.calls == 1


def test_ambiguous_note_cleanup_never_reaches_llm_or_filesystem(
    tmp_path: Path,
) -> None:
    llm = NeverLLM()

    harness = Harness(
        llm, ToolRegistry(), executor(tmp_path), ConversationMemory()
    )
    result = harness.processar_mensagem("dê uma limpada nas minhas notas")
    assert "listar as notas" in result
    assert "escolhe" in result
    assert llm.calls == 0
