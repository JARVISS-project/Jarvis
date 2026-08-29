from types import SimpleNamespace

from jarvis.remote.core_port import HarnessRemoteCorePort
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.security.risk import RiskLevel


class FakeHarness:
    def __init__(self) -> None:
        self.calls = []
        self.confirmation_sessions = SimpleNamespace(current=lambda: None)
        self.config = SimpleNamespace(
            browser_enabled=True,
            desktop_control_enabled=True,
            desktop_read_enabled=True,
            filesystem_read_enabled=True,
            filesystem_write_enabled=False,
            obsidian_read_enabled=True,
            obsidian_write_enabled=False,
            vision_enabled=True,
        )

    def processar_mensagem(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return "core-response"


def test_core_port_routes_chat_without_exposing_harness_surface() -> None:
    harness = FakeHarness()
    port = HarnessRemoteCorePort(harness)  # type: ignore[arg-type]

    assert port.process_chat("hello") == "core-response"
    assert harness.calls == [("hello", {"response_mode": "text"})]
    assert not hasattr(port, "tool_executor")
    assert not hasattr(port, "tool_registry")
    assert not hasattr(port, "harness")


def test_core_port_routes_confirmation_to_original_ids() -> None:
    harness = FakeHarness()
    port = HarnessRemoteCorePort(harness)  # type: ignore[arg-type]

    response = port.process_confirmation(
        confirmed=True,
        session_id="session-1",
        challenge_id="challenge-1",
    )

    assert response == "core-response"
    assert harness.calls == [
        (
            "sim",
            {
                "response_mode": "text",
                "confirmation_session_id": "session-1",
                "confirmation_challenge_id": "challenge-1",
            },
        )
    ]


def test_core_port_exposes_only_sanitized_confirmation_snapshot() -> None:
    harness = FakeHarness()
    harness.confirmation_sessions = SimpleNamespace(
        current=lambda: SimpleNamespace(
            id="session",
            challenge_id="challenge",
            prompt_text="Confirmar?",
            expires_at=50.0,
            reminder_count=1,
            invocation=SimpleNamespace(
                tool_name="dangerous_tool",
                target="target",
                risk=RiskLevel.CRITICAL,
                arguments={"secret": "value"},
            ),
        )
    )
    port = HarnessRemoteCorePort(harness)  # type: ignore[arg-type]

    snapshot = port.current_confirmation()

    assert snapshot is not None
    assert snapshot.session_id == "session"
    assert snapshot.risk == "CRITICAL"
    assert not hasattr(snapshot, "arguments")
    assert not hasattr(snapshot, "grant")


def test_core_port_exposes_only_aggregate_security_state() -> None:
    harness = FakeHarness()
    kill_switch = DesktopKillSwitch()
    kill_switch.trip("private local reason")
    port = HarnessRemoteCorePort(  # type: ignore[arg-type]
        harness,
        kill_switch,
    )

    snapshot = port.current_security()

    assert snapshot.kill_switch_tripped is True
    assert dict(snapshot.capabilities) == {
        "browser": True,
        "desktop_control": True,
        "desktop_read": True,
        "filesystem_read": True,
        "filesystem_write": False,
        "obsidian_read": True,
        "obsidian_write": False,
        "vision": True,
    }
    assert "private local reason" not in repr(snapshot)
    assert not hasattr(snapshot, "reset")
