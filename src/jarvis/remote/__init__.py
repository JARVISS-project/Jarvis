"""Remote Gateway - Acesso remoto seguro ao JARVIS."""

from jarvis.remote.auth import (
    Device,
    DeviceRegistry,
    PairingManager,
    PairingChallenge,
    TokenAuthenticator,
    generate_pairing_code,
    generate_token,
    hash_token,
    verify_token,
)
from jarvis.remote.bridge import RemoteGatewayBridge, PendingRemoteConfirmation
from jarvis.remote.core_port import (
    HarnessRemoteCorePort,
    RemoteConfirmation,
    RemoteCorePort,
    RemoteSecuritySnapshot,
)
from jarvis.remote.gateway import RemoteGateway
from jarvis.remote.protocol import (
    MessageType,
    AgentState,
    BaseMessage,
    ChatMessage,
    ChatResponse,
    StateSubscribe,
    TelemetrySubscribe,
    SecuritySubscribe,
    ConfirmationRequest,
    ConfirmationRespond,
    Error,
    Ping,
    Pong,
    AuthPair,
    AuthToken,
    AuthSuccess,
    AuthFailed,
    DevicePaired,
    DeviceRevoked,
    StateUpdate,
    TelemetryUpdate,
    SecurityUpdate,
    parse_message,
    serialize_message,
    MAX_MESSAGE_SIZE,
)
from jarvis.remote.sessions import (
    RemoteSession,
    RateLimiter,
    SessionManager,
)
from jarvis.remote.websocket_transport import (
    WebSocketTransportConnection,
    WebSocketTransportServer,
)

__all__ = [
    # Auth
    "Device",
    "DeviceRegistry",
    "PairingManager",
    "PairingChallenge",
    "TokenAuthenticator",
    "generate_pairing_code",
    "generate_token",
    "hash_token",
    "verify_token",
    # Bridge
    "RemoteGatewayBridge",
    "PendingRemoteConfirmation",
    "HarnessRemoteCorePort",
    "RemoteConfirmation",
    "RemoteCorePort",
    "RemoteSecuritySnapshot",
    # Gateway
    "RemoteGateway",
    # Protocol
    "MessageType",
    "AgentState",
    "BaseMessage",
    "ChatMessage",
    "ChatResponse",
    "StateSubscribe",
    "TelemetrySubscribe",
    "SecuritySubscribe",
    "ConfirmationRequest",
    "ConfirmationRespond",
    "Error",
    "Ping",
    "Pong",
    "AuthPair",
    "AuthToken",
    "AuthSuccess",
    "AuthFailed",
    "DevicePaired",
    "DeviceRevoked",
    "StateUpdate",
    "TelemetryUpdate",
    "SecurityUpdate",
    "parse_message",
    "serialize_message",
    "MAX_MESSAGE_SIZE",
    # Sessions
    "RemoteSession",
    "RateLimiter",
    "SessionManager",
    # WebSocket transport
    "WebSocketTransportConnection",
    "WebSocketTransportServer",
]
