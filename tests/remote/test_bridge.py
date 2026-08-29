from pathlib import Path

from jarvis.application.state import AgentState, AgentStateBus
from jarvis.application.telemetry import TelemetryBus
from jarvis.remote.bridge import RemoteGatewayBridge
from jarvis.remote.core_port import RemoteConfirmation, RemoteSecuritySnapshot
from jarvis.remote.protocol import (
    ChatMessage,
    ChatResponse,
    ConfirmationRequest,
    ConfirmationRespond,
    Error,
    StateSubscribe,
    StateUpdate,
    SecuritySubscribe,
    SecurityUpdate,
)
from jarvis.security.audit import AuditLogger


class FakeCorePort:
    def __init__(self) -> None:
        self.confirmation = None
        self.chat_calls = []
        self.confirmation_calls = []
        self.create_confirmation = False
        self.security = RemoteSecuritySnapshot(
            kill_switch_tripped=False,
            capabilities=(("vision", True),),
        )

    def process_chat(self, text: str) -> str:
        self.chat_calls.append(text)
        if self.create_confirmation:
            self.confirmation = RemoteConfirmation(
                session_id="core-session",
                challenge_id="core-challenge",
                action="critical_tool",
                target="document",
                risk="CRITICAL",
                prompt_text="Confirmar ação?",
                expires_at=100.0,
                reminder_count=0,
            )
        return "core response"

    def process_confirmation(self, *, confirmed, session_id, challenge_id):
        self.confirmation_calls.append((confirmed, session_id, challenge_id))
        self.confirmation = None
        return "executed" if confirmed else "cancelled"

    def current_confirmation(self):
        return self.confirmation

    def current_security(self):
        return self.security


def make_bridge(tmp_path: Path):
    core = FakeCorePort()
    state_bus = AgentStateBus()
    telemetry_bus = TelemetryBus()
    audit = AuditLogger(tmp_path / "audit.log")
    bridge = RemoteGatewayBridge(core, state_bus, telemetry_bus, audit)
    sent = []
    bridge.register_session_sender("remote-session", lambda message: sent.append(message) or True)
    bridge.start()
    return bridge, core, state_bus, sent


def test_bridge_routes_chat_only_through_core_port(tmp_path: Path) -> None:
    bridge, core, _state_bus, sent = make_bridge(tmp_path)
    try:
        bridge.handle_message(
            "remote-session",
            ChatMessage(text="hello", request_id="request"),
        )
    finally:
        bridge.stop()

    assert core.chat_calls == ["hello"]
    assert isinstance(sent[0], ChatResponse)
    assert sent[0].text == "core response"
    audit = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "tool=remote_gateway" in audit
    assert "target=chat:remote-session" in audit


def test_bridge_binds_confirmation_and_returns_decision_to_core(tmp_path: Path) -> None:
    bridge, core, _state_bus, sent = make_bridge(tmp_path)
    core.create_confirmation = True
    try:
        bridge.handle_message("remote-session", ChatMessage(text="do it"))
        request = next(item for item in sent if isinstance(item, ConfirmationRequest))

        bridge.handle_message(
            "remote-session",
            ConfirmationRespond(
                session_id=request.session_id,
                challenge_id=request.challenge_id,
                confirmed=True,
                request_id="confirm-request",
            ),
        )
    finally:
        bridge.stop()

    assert core.confirmation_calls == [
        (True, "core-session", "core-challenge")
    ]
    assert isinstance(sent[-1], ChatResponse)
    assert sent[-1].text == "executed"
    assert not hasattr(bridge, "confirmation_manager")


def test_bridge_rejects_confirmation_with_wrong_challenge(tmp_path: Path) -> None:
    bridge, core, _state_bus, sent = make_bridge(tmp_path)
    core.create_confirmation = True
    try:
        bridge.handle_message("remote-session", ChatMessage(text="do it"))
        bridge.handle_message(
            "remote-session",
            ConfirmationRespond(
                session_id="core-session",
                challenge_id="wrong",
                confirmed=True,
            ),
        )
    finally:
        bridge.stop()

    assert core.confirmation_calls == []
    assert isinstance(sent[-1], Error)
    assert sent[-1].code == "challenge_mismatch"


def test_disconnect_denies_original_confirmation_through_core(tmp_path: Path) -> None:
    bridge, core, _state_bus, _sent = make_bridge(tmp_path)
    core.create_confirmation = True
    bridge.handle_message("remote-session", ChatMessage(text="do it"))

    bridge.on_session_disconnect("remote-session")
    bridge.stop()

    assert core.confirmation_calls == [
        (False, "core-session", "core-challenge")
    ]


def test_state_subscription_never_exposes_snapshot_detail(tmp_path: Path) -> None:
    bridge, _core, state_bus, sent = make_bridge(tmp_path)
    try:
        state_bus.publish(
            AgentState.EXECUTING,
            source="tool_executor",
            detail="secret-target",
        )
        bridge.handle_message("remote-session", StateSubscribe())
    finally:
        bridge.stop()

    update = next(item for item in sent if isinstance(item, StateUpdate))
    assert update.state.value == "executing"
    assert not hasattr(update, "detail")


def test_security_subscription_exposes_only_aggregate_snapshot(tmp_path: Path) -> None:
    bridge, core, _state_bus, sent = make_bridge(tmp_path)
    try:
        bridge.handle_message(
            "remote-session",
            SecuritySubscribe(request_id="security"),
        )
    finally:
        bridge.stop()

    update = next(item for item in sent if isinstance(item, SecurityUpdate))
    assert update.request_id == "security"
    assert update.kill_switch_tripped is False
    assert update.capabilities == {"vision": True}
    assert not hasattr(update, "reason")
    assert not hasattr(update, "reset")
