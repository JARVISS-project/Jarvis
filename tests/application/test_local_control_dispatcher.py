from __future__ import annotations

import pytest

from jarvis.application.local_control_dispatcher import LocalControlDispatcher
from jarvis.local_control.models import ApplicationInstance, ApplicationState
from jarvis.local_control.service import LocalControlService
from jarvis.media.models import PlaybackState
from jarvis.media.service import GsmtcMediaService
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.local_control import local_control_tools
from jarvis.tools.media import media_tools
from jarvis.tools.registry import ToolRegistry
from tests.local_control.test_local_control import FakeBackend
from tests.media.test_gsmtc_service import FakeGsmtcAdapter


CONTRACT = {
    "version": 1, "commands": [
        {"tool": "local_application_open", "arguments": {"application": "spotify"}, "aliases": ["abra o spotify"]},
        {"tool": "local_application_open", "arguments": {"application": "obsidian"}, "aliases": ["abra o obsidian"]},
        {"tool": "local_application_open", "arguments": {"application": "default_browser"}, "aliases": ["abra o navegador"]},
        {"tool": "local_application_status", "arguments": {"application": "spotify"}, "aliases": ["o spotify está aberto"]},
        {"tool": "local_application_focus", "arguments": {"application": "spotify"}, "aliases": ["traga o spotify para frente"]},
        {"tool": "local_application_close", "arguments": {"application": "spotify"}, "aliases": ["feche o spotify"]},
        {"tool": "local_media_control", "arguments": {"action": "pause"}, "aliases": ["pause a música"]},
        {"tool": "local_media_control", "arguments": {"action": "play"}, "aliases": ["continue a música"]},
        {"tool": "local_media_control", "arguments": {"action": "next"}, "aliases": ["próxima faixa"]},
        {"tool": "local_media_control", "arguments": {"action": "previous"}, "aliases": ["faixa anterior"]},
        {"tool": "local_volume_control", "arguments": {"action": "increase"}, "aliases": ["aumente o volume"]},
        {"tool": "local_volume_control", "arguments": {"action": "decrease"}, "aliases": ["diminua o volume"]},
        {"tool": "local_volume_control", "arguments": {"action": "set", "percent": 50}, "aliases": ["defina o volume para cinquenta por cento"]},
        {"tool": "local_volume_control", "arguments": {"action": "mute"}, "aliases": ["ative o mudo"]},
        {"tool": "local_volume_control", "arguments": {"action": "unmute"}, "aliases": ["desative o mudo"]},
    ],
}


def build():
    backend = FakeBackend(); service = LocalControlService(backend, sleep=lambda _: None)
    registry = ToolRegistry()
    for tool in local_control_tools(service): registry.register(tool)
    media_adapter = FakeGsmtcAdapter(PlaybackState.PLAYING)
    media_adapter.after_state = PlaybackState.PAUSED
    for tool in media_tools(GsmtcMediaService(media_adapter)):
        registry.register(tool)
    rows = []
    for name in ("local_application_open", "local_application_status", "local_application_focus",
                 "local_application_close", "current_media", "local_media_control",
                 "local_volume_control"):
        tool = registry.get(name)
        rows.append({"id": name, "tool": name, "risk": tool.risk.value,
                     "implementation_proven": True, "readiness_proven": True,
                     "state": "implemented_tested", "parameters": tool.parameters})
    calls = []
    def execute(tool, arguments, _grant, interaction):
        calls.append((tool.name, arguments, interaction))
        if tool.name == "local_application_close":
            return ToolExecutionResult("Confirme fechar; trabalho não salvo pode ser perdido.", "confirmation_required", False)
        return ToolExecutionResult(tool.execute(**arguments), "success", True)
    return LocalControlDispatcher(registry, execute, catalog={
        "local_control_contract": CONTRACT, "capabilities": rows,
    }), backend, calls


@pytest.mark.parametrize("text,tool", [
    ("Abra o Spotify.", "local_application_open"),
    ("Abra o Obsidian.", "local_application_open"),
    ("O Spotify está aberto?", "local_application_status"),
    ("Pause a música.", "local_media_control"),
    ("Continue a música.", "local_media_control"),
    ("Próxima faixa.", "local_media_control"),
    ("Faixa anterior.", "local_media_control"),
    ("Aumente o volume.", "local_volume_control"),
    ("Diminua o volume.", "local_volume_control"),
    ("Defina o volume para cinquenta por cento.", "local_volume_control"),
    ("Ative o mudo.", "local_volume_control"),
    ("Desative o mudo.", "local_volume_control"),
])
def test_explicit_aliases_execute_once(text, tool):
    dispatcher, _backend, calls = build()
    interaction = dispatcher.new_interaction_id()
    first = dispatcher.dispatch(text, interaction_id=interaction)
    repeated = dispatcher.dispatch(text, interaction_id=interaction)
    assert first.executed and repeated == first
    assert len(calls) == 1 and calls[0][0] == tool


@pytest.mark.parametrize("text", [
    "Não abra o Spotify", "Não aumente o volume", "Não feche o Obsidian",
    "Abra o cmd", "Abra C:\\Windows\\cmd.exe", "Abra https://example.com",
    "Abra o Spotfy", "Abra o Spotify com argumentos", "Abra e feche o Spotify",
    "Defina o volume", "Defina o volume para cinco", "volume cento e um",
])
def test_adversarial_text_never_executes(text):
    dispatcher, backend, calls = build()
    result = dispatcher.dispatch(text)
    assert not result.executed and calls == [] and backend.calls == []


def test_close_requests_confirmation_without_backend_call():
    dispatcher, backend, calls = build()
    backend.states["spotify"] = ApplicationState(
        "spotify", (ApplicationInstance("spotify", 10, 20, "Window"),))
    result = dispatcher.dispatch("Feche o Spotify.")
    assert result.reason == "confirmation_required" and not result.executed
    assert "não salvo" in (result.response or "") and backend.calls == []
    assert len(calls) == 1


def test_invalid_catalog_and_stale_interaction_fail_closed():
    dispatcher, backend, calls = build()
    stale = dispatcher.dispatch("Abra o Spotify", interaction_id="old:1")
    assert stale.reason == "stale_interaction"
    dispatcher._catalog["capabilities"][0]["risk"] = "read_only"
    result = dispatcher.dispatch("Abra o Spotify")
    assert not result.executed and backend.calls == [] and calls == []


def test_media_tool_unavailable_never_executes():
    dispatcher, backend, calls = build()
    dispatcher._registry._tools.pop("local_media_control")  # noqa: SLF001
    result = dispatcher.dispatch("Próxima faixa.")
    assert result.reason == "capability_unavailable"
    assert not result.executed and backend.calls == [] and calls == []
