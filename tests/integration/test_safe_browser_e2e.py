from __future__ import annotations

from copy import deepcopy

import pytest

from jarvis.application.safe_browser_dispatcher import SafeBrowserDispatcher
from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.safe_browser.models import ValidatedUri
from jarvis.safe_browser.service import SafeBrowserService
from jarvis.security.audit import AuditLogger
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.safe_browser import safe_browser_tools
from jarvis.voice.semantic_shadow import load_catalog


class NoToolFallbackLlm(LLMClient):
    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        from jarvis.llm.types import LLMResponse

        assert tools == []
        return LLMResponse(content="Não executei.")


class FakeLauncher:
    def __init__(self) -> None:
        self.token: str | None = "association-a"
        self.calls: list[str] = []

    def association_token(self) -> str | None:
        return self.token

    def launch(self, request: ValidatedUri) -> bool:
        self.calls.append(request.canonical_url)
        return True


def build(tmp_path):  # type: ignore[no-untyped-def]
    launcher = FakeLauncher()
    registry = ToolRegistry()
    for tool in safe_browser_tools(SafeBrowserService(launcher)):
        registry.register(tool)
    config = JarvisConfig(settings_path=tmp_path / "missing.json")
    config.safe_browser_enabled = True
    audit_path = tmp_path / "audit.jsonl"
    harness = Harness(
        llm=NoToolFallbackLlm(), tool_registry=registry,
        tool_executor=ToolExecutor(SafetyManager(), AuditLogger(audit_path)),
        memory=ConversationMemory(), config=config,
    )
    catalog = deepcopy(load_catalog())
    for row in catalog["capabilities"]:
        if row["id"] in {"safe_browser_status", "safe_browser_destinations",
                          "safe_browser_open", "safe_web_search"}:
            row["readiness_proven"] = True
            row["state"] = "implemented_tested"
    harness.intent_coordinator.safe_browser_dispatcher = SafeBrowserDispatcher(
        registry, harness._execute_tool, catalog=catalog,  # noqa: SLF001
    )
    return harness, launcher, audit_path


def test_registered_destination_reaches_launcher_and_tts_once(tmp_path) -> None:
    harness, launcher, _audit = build(tmp_path)
    response = harness.processar_mensagem("Abra o GitHub.", response_mode="voice")
    assert launcher.calls == ["https://github.com/"]
    assert [response] == [response]
    assert "aceitou" in response and "carreg" not in response


def test_confirmed_search_is_bound_private_and_one_use(tmp_path) -> None:
    harness, launcher, audit_path = build(tmp_path)
    term = "café com pão"
    prompt = harness.processar_mensagem(f"Pesquise na web por {term}.")
    session = harness.confirmation_sessions.current()
    assert session is not None and term in prompt and "Google" in prompt
    assert session.expires_at - session.created_at == 30.0
    assert term not in session.invocation.target and launcher.calls == []
    response = harness.processar_mensagem(
        "sim", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    assert "aceitou" in response
    assert launcher.calls == ["https://www.google.com/search?q=caf%C3%A9+com+p%C3%A3o"]
    replay = harness.processar_mensagem(
        "sim", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    assert "não está mais ativa" in replay and len(launcher.calls) == 1
    assert term not in audit_path.read_text(encoding="utf-8")


def test_search_cancellation_and_wrong_confirmation_open_nothing(tmp_path) -> None:
    harness, launcher, _audit = build(tmp_path)
    harness.processar_mensagem("Pesquise no Google por gatos pretos.")
    session = harness.confirmation_sessions.current()
    assert session is not None
    stale = harness.processar_mensagem(
        "sim", confirmation_session_id="other",
        confirmation_challenge_id=session.challenge_id,
    )
    assert "corresponde" in stale and launcher.calls == []
    cancelled = harness.processar_mensagem(
        "não", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    assert "cancel" in cancelled.casefold() and launcher.calls == []


@pytest.mark.parametrize("text", [
    "Não abra o GitHub.", "Abra o GitHub e o YouTube.",
    "Abra https://github.com/.", "Execute safe_browser_open.",
    "Abra o Githab.", "Pesquise na web por exemplo.com.",
    "Pesquise por gatos e abra o YouTube.",
])
def test_adversarial_language_never_reaches_launcher(tmp_path, text) -> None:
    harness, launcher, _audit = build(tmp_path)
    harness.processar_mensagem(text)
    assert launcher.calls == []


def test_association_change_after_confirmation_fails_closed(tmp_path) -> None:
    harness, launcher, _audit = build(tmp_path)
    harness.processar_mensagem("Pesquise no Google por estrelas.")
    session = harness.confirmation_sessions.current()
    assert session is not None
    launcher.token = "association-b"
    response = harness.processar_mensagem(
        "sim", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    assert "não autorizada" in response and launcher.calls == []


def test_local_spotify_command_is_not_claimed_as_browser_domain(tmp_path) -> None:
    harness, launcher, _audit = build(tmp_path)
    decision = harness.intent_coordinator.safe_browser_dispatcher.dispatch(
        "Abra o Spotify."
    )
    assert decision.handled is False
    assert decision.executed is False
    assert launcher.calls == []
