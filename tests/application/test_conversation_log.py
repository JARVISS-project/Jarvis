from jarvis.application.conversation_log import ConversationLogBus


def test_conversation_log_keeps_only_recent_entries() -> None:
    bus = ConversationLogBus(max_entries=4)

    bus.add_user("um")
    bus.add_assistant("dois")
    bus.add_user("tres")
    bus.add_assistant("quatro")
    bus.add_user("cinco")

    assert [(e.role, e.text) for e in bus.snapshot.entries] == [
        ("assistant", "dois"),
        ("user", "tres"),
        ("assistant", "quatro"),
        ("user", "cinco"),
    ]


def test_conversation_log_ignores_empty_text() -> None:
    bus = ConversationLogBus()

    bus.add_user("   ")

    assert bus.snapshot.entries == ()
    assert bus.snapshot.sequence == 0
