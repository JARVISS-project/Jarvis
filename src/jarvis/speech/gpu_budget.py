"""Conservative, deterministic GPU admission policy for local STT."""
from __future__ import annotations

from dataclasses import dataclass
import subprocess
import threading
from typing import Callable, Literal


@dataclass(frozen=True, slots=True)
class GpuMemory:
    total_mib: int
    used_mib: int
    free_mib: int


@dataclass(frozen=True, slots=True)
class OllamaReclaimRequest:
    available: bool
    model_name: str | None = None
    owned_by_jarvis: bool = False


@dataclass(frozen=True, slots=True)
class GpuBudgetDecision:
    status: Literal["ready", "insufficient", "unavailable", "invalid"]
    required_mib: int
    memory: GpuMemory | None
    reclaim_attempted: bool = False
    reclaim_succeeded: bool = False


class GpuBudgetError(RuntimeError):
    def __init__(self, decision: GpuBudgetDecision) -> None:
        self.decision = decision
        if decision.status == "insufficient" and decision.memory is not None:
            message = (
                "VRAM insuficiente para o perfil STT: "
                f"livre={decision.memory.free_mib} MiB, "
                f"necessário={decision.required_mib} MiB."
            )
        else:
            message = "Orçamento de VRAM indisponível."
        super().__init__(message)


def query_gpu_memory(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> GpuMemory:
    try:
        result = runner(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=5.0,
        )
        values = [int(value.strip()) for value in result.stdout.splitlines()[0].split(",")]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as error:
        raise RuntimeError("Orçamento de VRAM indisponível.") from error
    if len(values) != 3:
        raise RuntimeError("Resposta inválida ao consultar orçamento de VRAM.")
    return GpuMemory(*values)


class GpuBudgetManager:
    """Serializes admission and only reclaims an explicitly owned Ollama model."""

    def __init__(
        self,
        probe: Callable[[], GpuMemory] = query_gpu_memory,
        *,
        ollama_state: Callable[[], OllamaReclaimRequest] | None = None,
        unload_owned_model: Callable[[str], bool] | None = None,
    ) -> None:
        self._probe = probe
        self._ollama_state = ollama_state
        self._unload_owned_model = unload_owned_model
        self._lock = threading.Lock()

    def assess(self, required_mib: int) -> GpuBudgetDecision:
        if required_mib < 0:
            raise ValueError("Estimativa de VRAM não pode ser negativa.")
        with self._lock:
            memory, status = self._measure()
            if status is not None:
                return GpuBudgetDecision(status, required_mib, memory)
            assert memory is not None
            if memory.free_mib >= required_mib:
                return GpuBudgetDecision("ready", required_mib, memory)
            request = self._ollama_state() if self._ollama_state else None
            may_reclaim = bool(
                request and request.available and request.model_name
                and request.owned_by_jarvis and self._unload_owned_model
            )
            if not may_reclaim:
                return GpuBudgetDecision("insufficient", required_mib, memory)
            succeeded = bool(self._unload_owned_model(request.model_name))
            if not succeeded:
                return GpuBudgetDecision("insufficient", required_mib, memory, True, False)
            measured, measured_status = self._measure()
            if measured_status is not None:
                return GpuBudgetDecision(measured_status, required_mib, measured, True, True)
            assert measured is not None
            return GpuBudgetDecision(
                "ready" if measured.free_mib >= required_mib else "insufficient",
                required_mib, measured, True, True,
            )

    def require(self, required_mib: int) -> GpuBudgetDecision:
        decision = self.assess(required_mib)
        if decision.status != "ready":
            raise GpuBudgetError(decision)
        return decision

    def _measure(
        self,
    ) -> tuple[GpuMemory | None, Literal["unavailable", "invalid"] | None]:
        try:
            memory = self._probe()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return None, "unavailable"
        if (
            not isinstance(memory, GpuMemory)
            or memory.total_mib <= 0
            or min(memory.used_mib, memory.free_mib) < 0
            or max(memory.used_mib, memory.free_mib) > memory.total_mib
        ):
            return memory if isinstance(memory, GpuMemory) else None, "invalid"
        return memory, None


def require_free_vram(required_mib: int, memory: GpuMemory) -> None:
    GpuBudgetManager(lambda: memory).require(required_mib)
