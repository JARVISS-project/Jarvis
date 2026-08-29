import json

import pytest

from jarvis.remote.protocol import (
    AgentState,
    AuthPair,
    AuthSuccess,
    ChatMessage,
    ConfirmationRespond,
    MAX_MESSAGE_SIZE,
    MessageType,
    ProtocolError,
    SecuritySubscribe,
    SecurityUpdate,
    StateUpdate,
    TelemetryUpdate,
    parse_message,
    serialize_message,
)


@pytest.mark.parametrize(
    "message",
    [
        ChatMessage(text="Olá", request_id="chat-1"),
        AuthPair(device_name="Notebook", pairing_code="123456"),
        ConfirmationRespond(
            session_id="session",
            challenge_id="challenge",
            confirmed=True,
        ),
        StateUpdate(state=AgentState.PROCESSING, sequence=3, source="core"),
        TelemetryUpdate(
            sequence=4,
            has_completed_turn=True,
            turn_ms=20,
            llm_ms=10,
            llm_calls=1,
            tool_ms=2,
            tool_calls=1,
            audit_events=2,
            audit_write_ok=True,
        ),
        SecuritySubscribe(),
        SecurityUpdate(
            sequence=2,
            kill_switch_tripped=False,
            capabilities={
                "browser": False,
                "desktop_control": False,
                "desktop_read": True,
                "filesystem_read": True,
                "filesystem_write": False,
                "obsidian_read": True,
                "obsidian_write": False,
                "vision": True,
            },
        ),
    ],
)
def test_protocol_round_trip_preserves_typed_message(message) -> None:
    encoded = serialize_message(message)

    decoded = parse_message(encoded.rstrip(b"\n"))

    assert decoded == message
    assert decoded.type is message.type


def test_message_type_is_not_constructor_controlled() -> None:
    with pytest.raises(TypeError):
        ChatMessage(text="Olá", type=MessageType.ERROR)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "unknown"},
        {"type": "chat.message"},
        {"type": "chat.message", "text": "ok", "extra": True},
        {"type": "chat.message", "text": 3},
        {"type": "state.subscribe", "subscribe": 1},
        {"type": "security.subscribe", "subscribe": 1},
        {
            "type": "security.update",
            "sequence": 1,
            "kill_switch_tripped": False,
            "capabilities": {"vision": True},
        },
        {
            "type": "confirmation.respond",
            "session_id": "s",
            "challenge_id": "c",
            "confirmed": "yes",
        },
        {
            "type": "chat.message",
            "text": "ok",
            "timestamp": "2026-01-01T00:00:00",
        },
        {"type": "auth.pair", "device_name": "Phone", "pairing_code": "12A456"},
    ],
)
def test_parse_message_rejects_invalid_or_non_strict_payload(payload) -> None:
    with pytest.raises(ProtocolError):
        parse_message(json.dumps(payload).encode("utf-8"))


def test_parse_message_rejects_non_object_and_oversize() -> None:
    with pytest.raises(ProtocolError):
        parse_message(b"[]")
    with pytest.raises(ProtocolError):
        parse_message(b"x" * (MAX_MESSAGE_SIZE + 1))


def test_auth_success_never_serializes_token() -> None:
    payload = json.loads(serialize_message(AuthSuccess(device_id="device")))

    assert payload["device_id"] == "device"
    assert "token" not in payload


def test_chat_length_is_bounded() -> None:
    payload = {
        "type": "chat.message",
        "text": "x" * (8 * 1024 + 1),
    }

    with pytest.raises(ProtocolError):
        parse_message(json.dumps(payload).encode("utf-8"))
