"""Protocolo lógico tipado do Remote Gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, ClassVar


MAX_MESSAGE_SIZE = 64 * 1024
MAX_CHAT_LENGTH = 8 * 1024
MAX_ID_LENGTH = 128
MAX_SHORT_TEXT_LENGTH = 256
REMOTE_CAPABILITIES = frozenset(
    {
        "browser",
        "desktop_control",
        "desktop_read",
        "filesystem_read",
        "filesystem_write",
        "obsidian_read",
        "obsidian_write",
        "vision",
    }
)


class ProtocolError(ValueError):
    """Mensagem remota inválida ou fora do schema permitido."""


class MessageType(Enum):
    CHAT_MESSAGE = "chat.message"
    STATE_SUBSCRIBE = "state.subscribe"
    TELEMETRY_SUBSCRIBE = "telemetry.subscribe"
    CONFIRMATION_RESPOND = "confirmation.respond"
    PING = "ping"
    AUTH_PAIR = "auth.pair"
    AUTH_TOKEN = "auth.token"
    CHAT_RESPONSE = "chat.response"
    STATE_UPDATE = "state.update"
    TELEMETRY_UPDATE = "telemetry.update"
    CONFIRMATION_REQUEST = "confirmation.request"
    ERROR = "error"
    PONG = "pong"
    AUTH_SUCCESS = "auth.success"
    AUTH_FAILED = "auth.failed"
    DEVICE_PAIRED = "device.paired"
    DEVICE_REVOKED = "device.revoked"
    SECURITY_SUBSCRIBE = "security.subscribe"
    SECURITY_UPDATE = "security.update"


class AgentState(Enum):
    WAITING_WAKE = "waiting_wake"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    WAITING_FOLLOWUP = "waiting_followup"
    WAITING_REPAIR = "waiting_repair"
    CONFIRMATION = "confirmation"
    EXECUTING = "executing"
    ERROR = "error"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True, kw_only=True)
class BaseMessage:
    type: ClassVar[MessageType]
    request_id: str | None = None
    timestamp: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": self.type.value,
            "timestamp": self.timestamp,
        }
        if self.request_id is not None:
            data["request_id"] = self.request_id
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatMessage(BaseMessage):
    type: ClassVar[MessageType] = MessageType.CHAT_MESSAGE
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "text": self.text}


@dataclass(frozen=True, slots=True, kw_only=True)
class StateSubscribe(BaseMessage):
    type: ClassVar[MessageType] = MessageType.STATE_SUBSCRIBE
    subscribe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "subscribe": self.subscribe}


@dataclass(frozen=True, slots=True, kw_only=True)
class TelemetrySubscribe(BaseMessage):
    type: ClassVar[MessageType] = MessageType.TELEMETRY_SUBSCRIBE
    subscribe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "subscribe": self.subscribe}


@dataclass(frozen=True, slots=True, kw_only=True)
class SecuritySubscribe(BaseMessage):
    type: ClassVar[MessageType] = MessageType.SECURITY_SUBSCRIBE
    subscribe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "subscribe": self.subscribe}


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationRespond(BaseMessage):
    type: ClassVar[MessageType] = MessageType.CONFIRMATION_RESPOND
    session_id: str
    challenge_id: str
    confirmed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "session_id": self.session_id,
            "challenge_id": self.challenge_id,
            "confirmed": self.confirmed,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Ping(BaseMessage):
    type: ClassVar[MessageType] = MessageType.PING


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthPair(BaseMessage):
    type: ClassVar[MessageType] = MessageType.AUTH_PAIR
    device_name: str
    pairing_code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "device_name": self.device_name,
            "pairing_code": self.pairing_code,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthToken(BaseMessage):
    type: ClassVar[MessageType] = MessageType.AUTH_TOKEN
    device_id: str
    token: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "device_id": self.device_id,
            "token": self.token,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatResponse(BaseMessage):
    type: ClassVar[MessageType] = MessageType.CHAT_RESPONSE
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "text": self.text}


@dataclass(frozen=True, slots=True, kw_only=True)
class StateUpdate(BaseMessage):
    type: ClassVar[MessageType] = MessageType.STATE_UPDATE
    state: AgentState
    sequence: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "state": self.state.value,
            "sequence": self.sequence,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class TelemetryUpdate(BaseMessage):
    type: ClassVar[MessageType] = MessageType.TELEMETRY_UPDATE
    sequence: int
    has_completed_turn: bool
    turn_ms: int
    llm_ms: int
    llm_calls: int
    tool_ms: int
    tool_calls: int
    audit_events: int
    audit_write_ok: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "sequence": self.sequence,
            "has_completed_turn": self.has_completed_turn,
            "turn_ms": self.turn_ms,
            "llm_ms": self.llm_ms,
            "llm_calls": self.llm_calls,
            "tool_ms": self.tool_ms,
            "tool_calls": self.tool_calls,
            "audit_events": self.audit_events,
            "audit_write_ok": self.audit_write_ok,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityUpdate(BaseMessage):
    type: ClassVar[MessageType] = MessageType.SECURITY_UPDATE
    sequence: int
    kill_switch_tripped: bool
    capabilities: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "sequence": self.sequence,
            "kill_switch_tripped": self.kill_switch_tripped,
            "capabilities": dict(self.capabilities),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationRequest(BaseMessage):
    type: ClassVar[MessageType] = MessageType.CONFIRMATION_REQUEST
    session_id: str
    challenge_id: str
    action: str
    target: str
    risk: str
    prompt_text: str
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "session_id": self.session_id,
            "challenge_id": self.challenge_id,
            "action": self.action,
            "target": self.target,
            "risk": self.risk,
            "prompt_text": self.prompt_text,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Error(BaseMessage):
    type: ClassVar[MessageType] = MessageType.ERROR
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True, kw_only=True)
class Pong(BaseMessage):
    type: ClassVar[MessageType] = MessageType.PONG


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthSuccess(BaseMessage):
    type: ClassVar[MessageType] = MessageType.AUTH_SUCCESS
    device_id: str

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "device_id": self.device_id}


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthFailed(BaseMessage):
    type: ClassVar[MessageType] = MessageType.AUTH_FAILED
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True, kw_only=True)
class DevicePaired(BaseMessage):
    type: ClassVar[MessageType] = MessageType.DEVICE_PAIRED
    device_id: str
    device_name: str
    token: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "device_id": self.device_id,
            "device_name": self.device_name,
            "token": self.token,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceRevoked(BaseMessage):
    type: ClassVar[MessageType] = MessageType.DEVICE_REVOKED
    device_id: str

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "device_id": self.device_id}


Message = (
    ChatMessage
    | StateSubscribe
    | TelemetrySubscribe
    | SecuritySubscribe
    | ConfirmationRespond
    | Ping
    | AuthPair
    | AuthToken
    | ChatResponse
    | StateUpdate
    | TelemetryUpdate
    | SecurityUpdate
    | ConfirmationRequest
    | Error
    | Pong
    | AuthSuccess
    | AuthFailed
    | DevicePaired
    | DeviceRevoked
)


COMMON_FIELDS = {"type", "request_id", "timestamp"}
MESSAGE_FIELDS: dict[MessageType, tuple[type[BaseMessage], set[str], set[str]]] = {
    MessageType.CHAT_MESSAGE: (ChatMessage, {"text"}, {"text"}),
    MessageType.STATE_SUBSCRIBE: (StateSubscribe, {"subscribe"}, set()),
    MessageType.TELEMETRY_SUBSCRIBE: (TelemetrySubscribe, {"subscribe"}, set()),
    MessageType.SECURITY_SUBSCRIBE: (SecuritySubscribe, {"subscribe"}, set()),
    MessageType.CONFIRMATION_RESPOND: (
        ConfirmationRespond,
        {"session_id", "challenge_id", "confirmed"},
        {"session_id", "challenge_id", "confirmed"},
    ),
    MessageType.PING: (Ping, set(), set()),
    MessageType.AUTH_PAIR: (
        AuthPair,
        {"device_name", "pairing_code"},
        {"device_name", "pairing_code"},
    ),
    MessageType.AUTH_TOKEN: (
        AuthToken,
        {"device_id", "token"},
        {"device_id", "token"},
    ),
    MessageType.CHAT_RESPONSE: (ChatResponse, {"text"}, {"text"}),
    MessageType.STATE_UPDATE: (
        StateUpdate,
        {"state", "sequence", "source"},
        {"state", "sequence", "source"},
    ),
    MessageType.TELEMETRY_UPDATE: (
        TelemetryUpdate,
        {
            "sequence", "has_completed_turn", "turn_ms", "llm_ms", "llm_calls",
            "tool_ms", "tool_calls", "audit_events", "audit_write_ok",
        },
        {
            "sequence", "has_completed_turn", "turn_ms", "llm_ms", "llm_calls",
            "tool_ms", "tool_calls", "audit_events", "audit_write_ok",
        },
    ),
    MessageType.SECURITY_UPDATE: (
        SecurityUpdate,
        {"sequence", "kill_switch_tripped", "capabilities"},
        {"sequence", "kill_switch_tripped", "capabilities"},
    ),
    MessageType.CONFIRMATION_REQUEST: (
        ConfirmationRequest,
        {"session_id", "challenge_id", "action", "target", "risk", "prompt_text", "expires_at"},
        {"session_id", "challenge_id", "action", "target", "risk", "prompt_text", "expires_at"},
    ),
    MessageType.ERROR: (Error, {"code", "message"}, {"code", "message"}),
    MessageType.PONG: (Pong, set(), set()),
    MessageType.AUTH_SUCCESS: (AuthSuccess, {"device_id"}, {"device_id"}),
    MessageType.AUTH_FAILED: (AuthFailed, {"code", "message"}, {"code", "message"}),
    MessageType.DEVICE_PAIRED: (
        DevicePaired,
        {"device_id", "device_name", "token"},
        {"device_id", "device_name", "token"},
    ),
    MessageType.DEVICE_REVOKED: (DeviceRevoked, {"device_id"}, {"device_id"}),
}


def _string(data: dict[str, Any], key: str, maximum: int, *, allow_empty: bool = False) -> str:
    value = data[key]
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        raise ProtocolError(f"Campo inválido: {key}")
    return value


def _integer(data: dict[str, Any], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"Campo inválido: {key}")
    return value


def _validate_common(data: dict[str, Any]) -> tuple[MessageType, str | None, str]:
    try:
        message_type = MessageType(data["type"])
    except (KeyError, TypeError, ValueError) as error:
        raise ProtocolError("Tipo de mensagem inválido") from error
    request_id = data.get("request_id")
    if request_id is not None and (
        not isinstance(request_id, str) or not request_id or len(request_id) > MAX_ID_LENGTH
    ):
        raise ProtocolError("request_id inválido")
    timestamp = data.get("timestamp", _utc_now())
    if not isinstance(timestamp, str) or len(timestamp) > MAX_SHORT_TEXT_LENGTH:
        raise ProtocolError("timestamp inválido")
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise ProtocolError("timestamp inválido") from error
    if parsed_timestamp.tzinfo is None:
        raise ProtocolError("timestamp deve conter timezone")
    return message_type, request_id, timestamp


def _construct(data: dict[str, Any], message_type: MessageType, request_id: str | None, timestamp: str) -> BaseMessage:
    common = {"request_id": request_id, "timestamp": timestamp}
    if message_type is MessageType.CHAT_MESSAGE:
        return ChatMessage(text=_string(data, "text", MAX_CHAT_LENGTH), **common)
    if message_type is MessageType.STATE_SUBSCRIBE:
        subscribe = data.get("subscribe", True)
        if not isinstance(subscribe, bool):
            raise ProtocolError("Campo inválido: subscribe")
        return StateSubscribe(subscribe=subscribe, **common)
    if message_type is MessageType.TELEMETRY_SUBSCRIBE:
        subscribe = data.get("subscribe", True)
        if not isinstance(subscribe, bool):
            raise ProtocolError("Campo inválido: subscribe")
        return TelemetrySubscribe(subscribe=subscribe, **common)
    if message_type is MessageType.SECURITY_SUBSCRIBE:
        subscribe = data.get("subscribe", True)
        if not isinstance(subscribe, bool):
            raise ProtocolError("Campo inválido: subscribe")
        return SecuritySubscribe(subscribe=subscribe, **common)
    if message_type is MessageType.CONFIRMATION_RESPOND:
        confirmed = data["confirmed"]
        if not isinstance(confirmed, bool):
            raise ProtocolError("Campo inválido: confirmed")
        return ConfirmationRespond(
            session_id=_string(data, "session_id", MAX_ID_LENGTH),
            challenge_id=_string(data, "challenge_id", MAX_ID_LENGTH),
            confirmed=confirmed,
            **common,
        )
    if message_type is MessageType.PING:
        return Ping(**common)
    if message_type is MessageType.AUTH_PAIR:
        code = _string(data, "pairing_code", 6)
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ProtocolError("pairing_code inválido")
        return AuthPair(
            device_name=_string(data, "device_name", 80),
            pairing_code=code,
            **common,
        )
    if message_type is MessageType.AUTH_TOKEN:
        return AuthToken(
            device_id=_string(data, "device_id", MAX_ID_LENGTH),
            token=_string(data, "token", MAX_SHORT_TEXT_LENGTH),
            **common,
        )
    if message_type is MessageType.CHAT_RESPONSE:
        return ChatResponse(text=_string(data, "text", MAX_CHAT_LENGTH, allow_empty=True), **common)
    if message_type is MessageType.STATE_UPDATE:
        try:
            state = AgentState(data["state"])
        except (TypeError, ValueError) as error:
            raise ProtocolError("Estado inválido") from error
        return StateUpdate(
            state=state,
            sequence=_integer(data, "sequence"),
            source=_string(data, "source", MAX_SHORT_TEXT_LENGTH),
            **common,
        )
    if message_type is MessageType.TELEMETRY_UPDATE:
        completed = data["has_completed_turn"]
        audit_ok = data["audit_write_ok"]
        if not isinstance(completed, bool) or (audit_ok is not None and not isinstance(audit_ok, bool)):
            raise ProtocolError("Telemetria inválida")
        return TelemetryUpdate(
            sequence=_integer(data, "sequence"),
            has_completed_turn=completed,
            turn_ms=_integer(data, "turn_ms"),
            llm_ms=_integer(data, "llm_ms"),
            llm_calls=_integer(data, "llm_calls"),
            tool_ms=_integer(data, "tool_ms"),
            tool_calls=_integer(data, "tool_calls"),
            audit_events=_integer(data, "audit_events"),
            audit_write_ok=audit_ok,
            **common,
        )
    if message_type is MessageType.SECURITY_UPDATE:
        kill_switch_tripped = data["kill_switch_tripped"]
        capabilities = data["capabilities"]
        if not isinstance(kill_switch_tripped, bool):
            raise ProtocolError("Estado do kill switch inválido")
        if (
            not isinstance(capabilities, dict)
            or set(capabilities) != REMOTE_CAPABILITIES
            or not all(isinstance(value, bool) for value in capabilities.values())
        ):
            raise ProtocolError("Capabilities inválidas")
        return SecurityUpdate(
            sequence=_integer(data, "sequence"),
            kill_switch_tripped=kill_switch_tripped,
            capabilities=dict(capabilities),
            **common,
        )
    if message_type is MessageType.CONFIRMATION_REQUEST:
        expires_at = data["expires_at"]
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or expires_at <= 0:
            raise ProtocolError("expires_at inválido")
        return ConfirmationRequest(
            session_id=_string(data, "session_id", MAX_ID_LENGTH),
            challenge_id=_string(data, "challenge_id", MAX_ID_LENGTH),
            action=_string(data, "action", MAX_SHORT_TEXT_LENGTH),
            target=_string(data, "target", MAX_CHAT_LENGTH),
            risk=_string(data, "risk", 32),
            prompt_text=_string(data, "prompt_text", MAX_CHAT_LENGTH),
            expires_at=float(expires_at),
            **common,
        )
    if message_type in {MessageType.ERROR, MessageType.AUTH_FAILED}:
        cls = Error if message_type is MessageType.ERROR else AuthFailed
        return cls(
            code=_string(data, "code", 64),
            message=_string(data, "message", MAX_CHAT_LENGTH),
            **common,
        )
    if message_type is MessageType.PONG:
        return Pong(**common)
    if message_type is MessageType.AUTH_SUCCESS:
        return AuthSuccess(device_id=_string(data, "device_id", MAX_ID_LENGTH), **common)
    if message_type is MessageType.DEVICE_PAIRED:
        return DevicePaired(
            device_id=_string(data, "device_id", MAX_ID_LENGTH),
            device_name=_string(data, "device_name", 80),
            token=_string(data, "token", MAX_SHORT_TEXT_LENGTH),
            **common,
        )
    if message_type is MessageType.DEVICE_REVOKED:
        return DeviceRevoked(device_id=_string(data, "device_id", MAX_ID_LENGTH), **common)
    raise ProtocolError("Tipo de mensagem não suportado")


def parse_message(raw: bytes) -> BaseMessage:
    """Decodifica exatamente uma mensagem JSON sem aceitar campos extras."""
    if not raw or len(raw) > MAX_MESSAGE_SIZE:
        raise ProtocolError("Tamanho de mensagem inválido")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("JSON inválido") from error
    if not isinstance(data, dict):
        raise ProtocolError("Mensagem deve ser um objeto JSON")
    message_type, request_id, timestamp = _validate_common(data)
    message_class, fields, required = MESSAGE_FIELDS[message_type]
    del message_class
    unknown = set(data) - COMMON_FIELDS - fields
    missing = required - set(data)
    if unknown:
        raise ProtocolError(f"Campos inesperados: {', '.join(sorted(unknown))}")
    if missing:
        raise ProtocolError(f"Campos ausentes: {', '.join(sorted(missing))}")
    return _construct(data, message_type, request_id, timestamp)


def serialize_message(message: BaseMessage) -> bytes:
    encoded = message.to_json().encode("utf-8")
    if len(encoded) > MAX_MESSAGE_SIZE:
        raise ProtocolError("Mensagem serializada excede o limite")
    return encoded + b"\n"
