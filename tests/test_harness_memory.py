from copy import deepcopy
from typing import Any

import pytest

from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.test_tool import TestTool


class ScriptedLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, Any]]] = []

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.calls.append(deepcopy(messages))
        return self.responses.pop(0)


class FakeToolExecutor:
    def execute(self, tool: TestTool, **kwargs: Any) -> str:
        return "RAW_TOOL_RESULT_MUST_NOT_BE_STORED"


def test_consecutive_interactions_send_context_to_llm() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(content="Seu nome é Kaynã."),
            LLMResponse(content="Seu nome continua sendo Kaynã."),
        ]
    )
    memory = ConversationMemory(max_messages=20)
    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=memory,
    )

    harness.processar_mensagem("Meu nome é Kaynã.")
    harness.processar_mensagem("Qual é meu nome?")

    assert llm.calls[1][1:] == [
        {"role": "user", "content": "Meu nome é Kaynã."},
        {"role": "assistant", "content": "Seu nome é Kaynã."},
        {"role": "user", "content": "Qual é meu nome?"},
    ]


def test_raw_tool_results_are_only_used_during_agent_loop() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="análise interna\n</think>",
                tool_calls=[ToolCallRequest(name="test_tool", arguments={})],
            ),
            LLMResponse(
                content=(
                    "<think>segredo final</think>Resposta natural final."
                )
            ),
        ]
    )
    registry = ToolRegistry()
    registry.register(TestTool())
    memory = ConversationMemory(max_messages=20)
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=memory,
    )

    harness.processar_mensagem("Use a ferramenta.")

    assert llm.calls[1][-1] == {
        "role": "tool",
        "tool_name": "test_tool",
        "content": "RAW_TOOL_RESULT_MUST_NOT_BE_STORED",
    }
    assert llm.calls[1][-2]["content"] == ""
    assert llm.calls[1][-2]["tool_calls"][0]["function"]["name"] == (
        "test_tool"
    )
    assert memory.get_history() == [
        {"role": "user", "content": "Use a ferramenta."},
        {"role": "assistant", "content": "Resposta natural final."},
    ]


def test_firewall_retry_does_not_execute_tool_again() -> None:
    class CountingExecutor(FakeToolExecutor):
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, tool: TestTool, **kwargs: Any) -> str:
            self.calls += 1
            return super().execute(tool, **kwargs)

    llm = ScriptedLLM(
        [
            LLMResponse(
                content="internal",
                tool_calls=[ToolCallRequest(name="test_tool", arguments={})],
            ),
            LLMResponse(content="Okay, the user asked... Wait, no..."),
            LLMResponse(content="A ferramenta foi executada com sucesso."),
        ]
    )
    executor = CountingExecutor()
    registry = ToolRegistry()
    registry.register(TestTool())
    memory = ConversationMemory()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=executor,  # type: ignore[arg-type]
        memory=memory,
    )

    response = harness.processar_mensagem("Execute a ferramenta.")

    assert response == "A ferramenta foi executada com sucesso."
    assert executor.calls == 1
    history = " ".join(item["content"] for item in memory.get_history())
    assert "Okay, the user asked" not in history
    assert "Wait, no" not in history


def test_one_hundred_leaks_never_enter_memory() -> None:
    for marker in (
        "Okay, the user asked...",
        "Wait, no...",
        "Looking at the tools...",
        "The assistant should call a tool.",
    ):
        for _ in range(25):
            memory = ConversationMemory()
            harness = make_harness(
                ScriptedLLM(
                    [
                        LLMResponse(content=marker),
                        LLMResponse(content="Resposta final segura."),
                    ]
                ),
                memory,
            )
            assert harness.processar_mensagem("Pergunta") == (
                "Resposta final segura."
            )
            assert marker not in str(memory.get_history())


def make_harness(
    llm: ScriptedLLM, memory: ConversationMemory
) -> Harness:
    return Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=memory,
    )


def test_memory_command_does_not_call_llm() -> None:
    llm = ScriptedLLM([])
    harness = make_harness(llm, ConversationMemory())

    result = harness.processar_mensagem("/memory")

    assert result == "Memória da conversa: 0 mensagens."
    assert llm.calls == []


def test_memory_command_is_not_added_to_history() -> None:
    memory = ConversationMemory()
    memory.add_user_message("Anterior")
    llm = ScriptedLLM([])
    harness = make_harness(llm, memory)

    harness.processar_mensagem("/memory")

    assert memory.get_history() == [
        {"role": "user", "content": "Anterior"}
    ]


def test_clear_command_does_not_call_llm() -> None:
    llm = ScriptedLLM([])
    harness = make_harness(llm, ConversationMemory())

    result = harness.processar_mensagem("/clear")

    assert result == "Memória da conversa limpa."
    assert llm.calls == []


def test_clear_command_removes_conversation_memory() -> None:
    memory = ConversationMemory()
    memory.add_user_message("Anterior")
    memory.add_assistant_message("Resposta anterior")
    harness = make_harness(ScriptedLLM([]), memory)

    harness.processar_mensagem("/clear")

    assert memory.count() == 0


def test_conversation_after_clear_has_no_previous_context() -> None:
    memory = ConversationMemory()
    memory.add_user_message("Contexto antigo")
    memory.add_assistant_message("Resposta antiga")
    llm = ScriptedLLM([LLMResponse(content="Nova resposta")])
    harness = make_harness(llm, memory)

    harness.processar_mensagem("/clear")
    harness.processar_mensagem("Nova pergunta")

    assert llm.calls[0][0]["role"] == "system"
    assert llm.calls[0][1:] == [
        {"role": "user", "content": "Nova pergunta"}
    ]


def make_persistent_harness(
    llm: ScriptedLLM,
    memory: ConversationMemory,
    memory_store: InMemoryMemoryStore,
    config: JarvisConfig | None = None,
) -> Harness:
    return Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=memory,
        memory_store=memory_store,
        config=config,
    )


def test_remember_does_not_call_llm_and_persists() -> None:
    llm = ScriptedLLM([])
    store = InMemoryMemoryStore()
    harness = make_persistent_harness(llm, ConversationMemory(), store)

    result = harness.processar_mensagem(
        "/remember Meu editor preferido é VS Code"
    )

    memories = store.get_all()
    assert llm.calls == []
    assert len(memories) == 1
    assert memories[0].content == "Meu editor preferido é VS Code"
    assert memories[0].category == "user"
    assert memories[0].id in result


def test_memories_lists_ids_categories_and_contents() -> None:
    store = InMemoryMemoryStore()
    entry = store.add("Uso Python", "user")
    harness = make_persistent_harness(
        ScriptedLLM([]), ConversationMemory(), store
    )

    result = harness.processar_mensagem("/memories")

    assert entry.id in result
    assert entry.category in result
    assert entry.content in result


def test_forget_removes_memory() -> None:
    store = InMemoryMemoryStore()
    entry = store.add("Remover", "user")
    harness = make_persistent_harness(
        ScriptedLLM([]), ConversationMemory(), store
    )

    result = harness.processar_mensagem(f"/forget {entry.id}")

    assert result == "Memória removida."
    assert store.get_all() == []


@pytest.mark.parametrize(
    "command",
    [
        "/remember Informação explícita",
        "/memories",
        "/forget id-inexistente",
    ],
)
def test_persistent_memory_commands_do_not_enter_conversation(
    command: str,
) -> None:
    memory = ConversationMemory()
    harness = make_persistent_harness(
        ScriptedLLM([]), memory, InMemoryMemoryStore()
    )

    harness.processar_mensagem(command)

    assert memory.get_history() == []


def test_persistent_memories_are_sent_as_separate_context() -> None:
    llm = ScriptedLLM([LLMResponse(content="Resposta")])
    store = InMemoryMemoryStore()
    entry = store.add("Meu editor é VS Code", "user")
    harness = make_persistent_harness(llm, ConversationMemory(), store)

    harness.processar_mensagem("Qual editor eu uso?")

    assert llm.calls[0][0]["role"] == "system"
    assert "explicitamente pelo usuário" in llm.calls[0][0]["content"]
    assert entry.id in llm.calls[0][0]["content"]
    assert entry.content in llm.calls[0][0]["content"]
    assert llm.calls[0][1] == {
        "role": "user",
        "content": "Qual editor eu uso?",
    }


def test_normal_message_does_not_create_persistent_memory() -> None:
    store = InMemoryMemoryStore()
    harness = make_persistent_harness(
        ScriptedLLM([LLMResponse(content="Resposta")]),
        ConversationMemory(),
        store,
    )

    harness.processar_mensagem("Guarde isto sem comando explícito")

    assert store.get_all() == []


def test_raw_tool_result_does_not_become_persistent_memory() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(name="test_tool", arguments={})],
            ),
            LLMResponse(content="Resposta final"),
        ]
    )
    registry = ToolRegistry()
    registry.register(TestTool())
    store = InMemoryMemoryStore()
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=FakeToolExecutor(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
        memory_store=store,
    )

    harness.processar_mensagem("Use a ferramenta")

    assert store.get_all() == []


def test_harness_uses_configured_tool_iteration_limit() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(name="missing", arguments={})],
            )
        ]
    )
    config = JarvisConfig(max_tool_iterations=1)
    harness = make_persistent_harness(
        llm,
        ConversationMemory(),
        InMemoryMemoryStore(),
        config,
    )

    result = harness.processar_mensagem("Teste")

    assert result == "Limite de chamadas de ferramentas atingido."
    assert len(llm.calls) == 1
