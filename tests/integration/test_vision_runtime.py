from pathlib import Path

from jarvis.application.state import AgentState, AgentStateBus
from jarvis.application.tool_execution_state import AgentToolExecutionObserver
from jarvis.__main__ import build_harness
from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.vision import VisionObserveScreenChangeTool
from jarvis.vision.models import CapturedFrame
from jarvis.vision.service import VisionService


class NeverLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("observação visual determinística não usa LLM")


class FakeVisionBackend:
    def __init__(self) -> None:
        self.frames = [
            CapturedFrame(2, 1, "RGB", b"secret"),
            CapturedFrame(2, 1, "RGB", b"public"),
        ]

    def capture_screen(self) -> CapturedFrame:
        return self.frames.pop(0)


def test_vision_runtime_is_authorized_audited_and_has_no_raw_output(
    tmp_path: Path,
) -> None:
    policy = CapabilityPolicy({Capability.VISION: True})
    registry = ToolRegistry()
    registry.register(
        VisionObserveScreenChangeTool(
            VisionService(FakeVisionBackend(), policy)
        )
    )
    bus = AgentStateBus(AgentState.PROCESSING)
    seen: list[AgentState] = []
    bus.subscribe(
        lambda snapshot: seen.append(snapshot.state),
        replay_current=False,
    )
    audit_path = tmp_path / "audit.log"
    llm = NeverLLM()
    from jarvis.security.policy import SecurityPolicy

    class ObserveOnlyPolicy(SecurityPolicy):
        def requires_confirmation(self, risk):
            return False

    safety = SafetyManager()
    safety.policy = ObserveOnlyPolicy()

    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=ToolExecutor(
            safety,
            AuditLogger(audit_path),
            capability_policy=policy,
            observer=AgentToolExecutionObserver(bus),
        ),
        memory=ConversationMemory(),
    )

    baseline = harness.processar_mensagem("Observe se a tela mudou.")
    changed = harness.processar_mensagem("Verifique se a tela mudou.")

    assert "baseline local registrada" in baseline
    assert "mudança visual detectada" in changed
    assert "secret" not in baseline + changed
    assert "public" not in baseline + changed
    assert llm.calls == 0
    assert seen == [
        AgentState.EXECUTING,
        AgentState.PROCESSING,
        AgentState.EXECUTING,
        AgentState.PROCESSING,
    ]
    audit = audit_path.read_text(encoding="utf-8")
    assert audit.count("tool=vision_observe_screen_change") == 2
    assert "target=desktop:screen" in audit
    assert "secret" not in audit


def test_disabled_vision_returns_local_unavailable_response() -> None:
    llm = NeverLLM()

    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=object(),  # type: ignore[arg-type]
        memory=ConversationMemory(),
    )

    response = harness.processar_mensagem("Compare a tela agora.")

    assert response == "Vision read-only não está disponível nesta execução."
    assert llm.calls == 0


def test_application_builder_registers_vision_only_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("JARVIS_VISION", raising=False)
    common = {
        "settings_path": tmp_path / "missing-settings.json",
        "database_path": tmp_path / "jarvis.db",
        "audit_log_path": tmp_path / "audit.log",
        "skill_store_path": tmp_path / "skills.json",
        "filesystem_read_enabled": False,
        "filesystem_write_enabled": False,
        "obsidian_enabled": False,
    }

    disabled = build_harness(
        JarvisConfig(vision_enabled=False, **common),
        ResourceManager(),
    )
    enabled = build_harness(
        JarvisConfig(vision_enabled=True, **common),
        ResourceManager(),
    )
    try:
        assert disabled.tool_registry.get("vision_observe_screen_change") is None
        assert enabled.tool_registry.get("vision_observe_screen_change") is not None
    finally:
        disabled.close_confirmation_sessions()
        enabled.close_confirmation_sessions()
