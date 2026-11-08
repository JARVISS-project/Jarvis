from __future__ import annotations

import threading

from jarvis.application.safe_browser_dispatcher import SafeBrowserDispatcher
from jarvis.safe_browser.policy import UrlPolicy
from jarvis.safe_browser.service import SafeBrowserService
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.safe_browser import safe_browser_tools
from jarvis.voice.semantic_shadow import load_catalog
from tests.safe_browser.test_policy_service import FakeLauncher


def build():
    policy = UrlPolicy(); launcher = FakeLauncher(policy)
    registry = ToolRegistry()
    for tool in safe_browser_tools(SafeBrowserService(launcher, policy)):
        registry.register(tool)
    catalog = load_catalog()
    for row in catalog["capabilities"]:
        if row["id"].startswith("safe_browser_") or row["id"] == "safe_web_search":
            row["readiness_proven"] = True
    calls = []
    def execute(tool, arguments, _grant, interaction):
        calls.append((tool.name, arguments, interaction))
        if tool.name == "safe_web_search":
            return ToolExecutionResult("confirme", "confirmation_required", False)
        return ToolExecutionResult(tool.execute(**arguments), "success", True)
    return SafeBrowserDispatcher(registry, execute, catalog=catalog), launcher, calls


def test_exact_destination_and_duplicate_interaction_execute_once():
    dispatcher, launcher, calls = build()
    interaction = dispatcher.new_interaction_id()
    barrier = threading.Barrier(3); results = []
    def invoke():
        barrier.wait(); results.append(dispatcher.dispatch("Abra o GitHub.", interaction_id=interaction))
    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join()
    assert results[0] == results[1]
    assert len(launcher.calls) == 1 and len(calls) == 1


def test_restart_and_missing_tool_fail_closed():
    dispatcher, launcher, calls = build()
    stale = dispatcher.dispatch("Abra o YouTube.", interaction_id="old:1")
    assert stale.reason == "stale_interaction" and launcher.calls == []
    dispatcher._registry._tools.pop("safe_browser_open")  # noqa: SLF001
    missing = dispatcher.dispatch("Abra o YouTube.")
    assert missing.reason == "capability_unavailable" and calls == []


def test_search_goes_to_confirmation_without_launch():
    dispatcher, launcher, calls = build()
    result = dispatcher.dispatch("Pesquise na web por café com pão.")
    assert result.reason == "confirmation_required"
    assert calls[0][1] == {"provider": "google", "term": "café com pão"}
    assert launcher.calls == []


def test_negation_two_actions_url_tool_name_and_extra_text_are_blocked():
    dispatcher, launcher, calls = build()
    texts = [
        "Não abra o GitHub.", "Abra o GitHub e o YouTube.",
        "Abra https://github.com/", "Execute safe_browser_open.",
        "Abra o GitHub com parâmetros.", "Abra o Githab.",
        "Pesquise na web por exemplo.com", "Pesquise por gatos e abra o YouTube",
    ]
    for text in texts:
        result = dispatcher.dispatch(text)
        assert not result.executed
    assert launcher.calls == [] and calls == []
