from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading

import pytest

from jarvis.speech.gpu_budget import (
    GpuBudgetError,
    GpuBudgetManager,
    GpuMemory,
    OllamaReclaimRequest,
    query_gpu_memory,
)

REQUIRED = 5781


@pytest.mark.parametrize(("free", "status"), [
    (7000, "ready"), (REQUIRED, "ready"), (REQUIRED - 1, "insufficient"),
])
def test_budget_boundaries_are_deterministic(free: int, status: str) -> None:
    memory = GpuMemory(12288, 12288 - free, free)
    decision = GpuBudgetManager(lambda: memory).assess(REQUIRED)
    assert decision.status == status
    assert decision.required_mib == REQUIRED
    assert decision.memory == memory


def test_probe_unavailable_and_invalid_are_distinct() -> None:
    unavailable = GpuBudgetManager(
        lambda: (_ for _ in ()).throw(RuntimeError("no nvidia-smi"))
    ).assess(REQUIRED)
    invalid = GpuBudgetManager(lambda: GpuMemory(0, 0, 0)).assess(REQUIRED)
    assert unavailable.status == "unavailable" and unavailable.memory is None
    assert invalid.status == "invalid" and invalid.memory == GpuMemory(0, 0, 0)


def test_query_rejects_invalid_probe_response() -> None:
    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "not,numbers\n", "")
    with pytest.raises(RuntimeError, match="indisponível"):
        query_gpu_memory(runner)


@pytest.mark.parametrize("state", [
    None,
    OllamaReclaimRequest(False),
    OllamaReclaimRequest(True, "external:model", False),
    OllamaReclaimRequest(True, None, False),
])
def test_absent_or_unowned_ollama_is_never_unloaded(state) -> None:
    calls: list[str] = []
    manager = GpuBudgetManager(
        lambda: GpuMemory(12288, 7000, 5288),
        ollama_state=(lambda: state) if state is not None else None,
        unload_owned_model=lambda model: calls.append(model) is None,
    )
    assert manager.assess(REQUIRED).status == "insufficient"
    assert calls == []


def test_owned_jarvis_model_is_unloaded_then_remeasured() -> None:
    readings = iter((GpuMemory(12288, 7000, 5288), GpuMemory(12288, 5000, 7288)))
    calls: list[str] = []
    manager = GpuBudgetManager(
        lambda: next(readings),
        ollama_state=lambda: OllamaReclaimRequest(True, "jarvis:model", True),
        unload_owned_model=lambda model: calls.append(model) is None,
    )
    decision = manager.assess(REQUIRED)
    assert decision.status == "ready"
    assert decision.reclaim_attempted and decision.reclaim_succeeded
    assert decision.memory == GpuMemory(12288, 5000, 7288)
    assert calls == ["jarvis:model"]


def test_failed_owned_unload_preserves_insufficient_result() -> None:
    probes = 0
    def probe() -> GpuMemory:
        nonlocal probes
        probes += 1
        return GpuMemory(12288, 7000, 5288)
    manager = GpuBudgetManager(
        probe,
        ollama_state=lambda: OllamaReclaimRequest(True, "jarvis:model", True),
        unload_owned_model=lambda _model: False,
    )
    decision = manager.assess(REQUIRED)
    assert decision.status == "insufficient"
    assert decision.reclaim_attempted and not decision.reclaim_succeeded
    assert probes == 1


def test_successful_unload_that_does_not_free_enough_stays_blocked() -> None:
    readings = iter((GpuMemory(12288, 7000, 5288), GpuMemory(12288, 6800, 5488)))
    manager = GpuBudgetManager(
        lambda: next(readings),
        ollama_state=lambda: OllamaReclaimRequest(True, "jarvis:model", True),
        unload_owned_model=lambda _model: True,
    )
    decision = manager.assess(REQUIRED)
    assert decision.status == "insufficient" and decision.reclaim_succeeded
    with pytest.raises(GpuBudgetError):
        GpuBudgetManager(lambda: decision.memory).require(REQUIRED)


def test_concurrent_requests_are_serialized_and_recovery_is_reversible() -> None:
    lock = threading.Lock()
    free = REQUIRED - 1
    unloads = 0
    def probe() -> GpuMemory:
        with lock:
            return GpuMemory(12288, 12288 - free, free)
    def unload(_model: str) -> bool:
        nonlocal free, unloads
        with lock:
            unloads += 1
            free = REQUIRED + 100
        return True
    manager = GpuBudgetManager(
        probe,
        ollama_state=lambda: OllamaReclaimRequest(True, "jarvis:model", True),
        unload_owned_model=unload,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(pool.map(lambda _index: manager.assess(REQUIRED), range(2)))
    assert [item.status for item in decisions] == ["ready", "ready"]
    assert unloads == 1
    assert sum(item.reclaim_attempted for item in decisions) == 1
    free = REQUIRED - 1
    assert manager.assess(REQUIRED).status == "ready"


def test_failure_never_changes_requested_profile_or_kills_process() -> None:
    killed: list[int] = []
    profile = ("large-v3", "cuda", "float16")
    manager = GpuBudgetManager(lambda: GpuMemory(12288, 7000, 5288))
    with pytest.raises(GpuBudgetError):
        manager.require(REQUIRED)
    assert profile == ("large-v3", "cuda", "float16")
    assert killed == []
