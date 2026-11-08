from __future__ import annotations

from jarvis.config import JarvisConfig
import threading
import pytest
from copy import deepcopy

from jarvis.application.local_control_dispatcher import LocalControlDispatcher
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.local_control.models import ApplicationInstance, ApplicationState
from jarvis.local_control.service import LocalControlService
from jarvis.media.models import PlaybackState
from jarvis.media.service import GsmtcMediaService
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.audit import AuditLogger
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.local_control import local_control_tools
from jarvis.tools.media import media_tools
from jarvis.tools.registry import ToolRegistry
from tests.local_control.test_local_control import FakeBackend
from tests.media.test_gsmtc_service import FakeGsmtcAdapter
from jarvis.voice.semantic_shadow import load_catalog


class NeverLlm(LLMClient):
    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        raise AssertionError("local control command must not reach LLM")


def build(tmp_path):
    backend = FakeBackend()
    service = LocalControlService(backend, sleep=lambda _: None)
    registry = ToolRegistry()
    for tool in local_control_tools(service): registry.register(tool)
    media_adapter = FakeGsmtcAdapter(PlaybackState.PLAYING)
    media_adapter.after_state = PlaybackState.PAUSED
    backend.media_adapter = media_adapter
    for tool in media_tools(GsmtcMediaService(media_adapter)):
        registry.register(tool)
    config = JarvisConfig(settings_path=tmp_path / "missing.json")
    config.local_control_enabled = True
    executor = ToolExecutor(SafetyManager(), AuditLogger(tmp_path / "audit.jsonl"))
    harness = Harness(llm=NeverLlm(), tool_registry=registry, tool_executor=executor,
                      memory=ConversationMemory(), config=config)
    catalog = deepcopy(load_catalog())
    for row in catalog["capabilities"]:
        if row["id"] in {"local_media_control", "local_volume_control"}:
            row["readiness_proven"] = True
    harness.intent_coordinator.local_control_dispatcher = LocalControlDispatcher(
        registry, harness._execute_tool, catalog=catalog,  # noqa: SLF001
    )
    return harness, backend


def test_injected_transcript_to_fake_backend_and_tts_executes_once(tmp_path):
    harness, backend = build(tmp_path)
    sink = []
    response = harness.processar_mensagem("Abra o Spotify.", response_mode="voice")
    sink.append(response)
    assert backend.calls == [("open", "spotify")]
    assert "aberto e verificado" in response and sink == [response]
    assert harness.get_last_latency()["tool_calls"] == 1


def test_media_and_volume_e2e_use_fake_backend_only(tmp_path):
    harness, backend = build(tmp_path)
    assert "paus" in harness.processar_mensagem("Pause a música.")
    assert "50" in harness.processar_mensagem(
        "Defina o volume para cinquenta por cento."
    )
    assert backend.media_adapter.calls == ["pause"]
    assert backend.calls == [("volume", 50)]


@pytest.mark.parametrize("text,expected", [
    ("Pause a música.", "pause"),
    ("Continue a música.", "play"),
    ("Reproduza a música.", "play"),
    ("Próxima faixa.", "next"),
    ("Pule para a próxima faixa.", "next"),
    ("Faixa anterior.", "previous"),
    ("Volte para a faixa anterior.", "previous"),
])
def test_all_explicit_media_aliases_execute_exactly_once(tmp_path, text, expected):
    harness, backend = build(tmp_path)
    adapter = backend.media_adapter
    if expected == "play":
        adapter.state = PlaybackState.PAUSED
        adapter.after_state = PlaybackState.PLAYING
    elif expected == "pause":
        adapter.state = PlaybackState.PLAYING
        adapter.after_state = PlaybackState.PAUSED
    response = harness.processar_mensagem(text, response_mode="voice")
    tts_sink = [response]
    assert tts_sink == [response]
    assert adapter.calls == [expected]


@pytest.mark.parametrize("text", [
    "Não pause a música.",
    "Pause e reproduza a música.",
    "Pause no Spotify.",
    "Reproduza a música Canção.",
    "Execute local_media_control.",
    "Próxima faixa duas vezes.",
])
def test_media_language_blocks_never_reach_tool(tmp_path, text):
    harness, backend = build(tmp_path)
    harness.processar_mensagem(text, response_mode="voice")
    assert backend.media_adapter.calls == []


def test_current_media_metadata_reaches_only_requested_response(tmp_path):
    harness, backend = build(tmp_path)
    response = harness.processar_mensagem(
        "Qual música está tocando?", response_mode="voice"
    )
    assert "Canção çã 🎵" in response and "Artista Único" in response
    assert backend.media_adapter.calls == []
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "Canção" not in audit and "Artista" not in audit


def test_same_interaction_concurrent_replay_executes_once(tmp_path):
    harness, backend = build(tmp_path)
    dispatcher = harness.intent_coordinator.local_control_dispatcher
    interaction = dispatcher.new_interaction_id()
    barrier = threading.Barrier(3)
    results = []

    def invoke():
        barrier.wait()
        results.append(dispatcher.dispatch(
            "Próxima faixa.", interaction_id=interaction
        ))

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert len(results) == 2 and results[0] == results[1]
    assert backend.media_adapter.calls == ["next"]


def test_close_confirmation_is_bound_one_use_and_revalidates_identity(tmp_path):
    harness, backend = build(tmp_path)
    instance = ApplicationInstance("spotify", 10, 20, "Window")
    backend.states["spotify"] = ApplicationState("spotify", (instance,))
    prompt = harness.processar_mensagem("Feche o Spotify.")
    session = harness.confirmation_sessions.current()
    assert session is not None and "não salvo" in prompt and backend.calls == []
    response = harness.processar_mensagem(
        "sim, pode fazer", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    assert "fechado" in response and backend.calls == [("close", "spotify", 20)]
    replay = harness.processar_mensagem(
        "sim, pode fazer", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    assert "não está mais ativa" in replay and len(backend.calls) == 1


def test_close_confirmation_rejects_pid_change(tmp_path):
    harness, backend = build(tmp_path)
    instance = ApplicationInstance("spotify", 10, 20, "Window")
    backend.states["spotify"] = ApplicationState("spotify", (instance,))
    harness.processar_mensagem("Feche o Spotify.")
    session = harness.confirmation_sessions.current(); assert session is not None
    backend.states["spotify"] = ApplicationState(
        "spotify", (ApplicationInstance("spotify", 99, 20, "Window"),))
    response = harness.processar_mensagem(
        "sim", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    assert "não autorizada" in response and backend.calls == []


def test_flag_off_leaves_local_command_unhandled(tmp_path):
    backend = FakeBackend(); service = LocalControlService(backend)
    registry = ToolRegistry()
    for tool in local_control_tools(service): registry.register(tool)
    config = JarvisConfig(settings_path=tmp_path / "missing.json")
    config.local_control_enabled = False
    class EchoLlm(LLMClient):
        def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
            from jarvis.llm.types import LLMResponse
            return LLMResponse(content="fallback")
    harness = Harness(llm=EchoLlm(), tool_registry=registry,
                      tool_executor=ToolExecutor(SafetyManager(), AuditLogger(tmp_path / "a.jsonl")),
                      memory=ConversationMemory(), config=config)
    assert harness.processar_mensagem("Abra o Spotify.") == "fallback"
    assert backend.calls == []
