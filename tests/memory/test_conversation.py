import pytest

from jarvis.memory.conversation import ConversationMemory


def test_message_can_be_added() -> None:
    memory = ConversationMemory()

    memory.add_user_message("Olá")

    assert memory.get_history() == [{"role": "user", "content": "Olá"}]


def test_history_preserves_message_order() -> None:
    memory = ConversationMemory()

    memory.add_user_message("Primeira")
    memory.add_assistant_message("Segunda")
    memory.add_user_message("Terceira")

    assert memory.get_history() == [
        {"role": "user", "content": "Primeira"},
        {"role": "assistant", "content": "Segunda"},
        {"role": "user", "content": "Terceira"},
    ]


def test_maximum_message_limit_removes_oldest_messages() -> None:
    memory = ConversationMemory(max_messages=2)

    memory.add_user_message("Antiga")
    memory.add_assistant_message("Intermediária")
    memory.add_user_message("Recente")

    assert memory.get_history() == [
        {"role": "assistant", "content": "Intermediária"},
        {"role": "user", "content": "Recente"},
    ]


def test_history_is_returned_as_a_copy() -> None:
    memory = ConversationMemory()
    memory.add_user_message("Original")

    history = memory.get_history()
    history[0]["content"] = "Alterado"

    assert memory.get_history()[0]["content"] == "Original"


def test_message_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ConversationMemory(max_messages=0)


def test_clear_removes_all_messages() -> None:
    memory = ConversationMemory()
    memory.add_user_message("Pergunta")
    memory.add_assistant_message("Resposta")

    memory.clear()

    assert memory.get_history() == []


def test_count_returns_current_message_count() -> None:
    memory = ConversationMemory()
    assert memory.count() == 0

    memory.add_user_message("Pergunta")
    memory.add_assistant_message("Resposta")

    assert memory.count() == 2
