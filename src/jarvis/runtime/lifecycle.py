"""Lifecycle observável e cleanup previsível do runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import threading


class RuntimeState(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    voice: bool = False
    remote: bool = False
    ui: bool = False

    def enabled(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, enabled in (
                ("voice", self.voice),
                ("remote", self.remote),
                ("ui", self.ui),
            )
            if enabled
        )


class RuntimeLifecycle:
    """Owns cleanup callbacks in reverse startup order."""

    def __init__(
        self,
        on_state_change: Callable[[RuntimeState], None] | None = None,
    ) -> None:
        self._state = RuntimeState.STOPPED
        self._cleanup: list[Callable[[], None]] = []
        self._finalizers: list[Callable[[], None]] = []
        self._lock = threading.RLock()
        self._on_state_change = on_state_change

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    def begin_startup(self) -> None:
        with self._lock:
            if self._state is not RuntimeState.STOPPED:
                raise RuntimeError("Runtime já foi iniciado.")
            self._set_state(RuntimeState.STARTING)

    def add_cleanup(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._state not in {RuntimeState.STARTING, RuntimeState.RUNNING}:
                raise RuntimeError("Cleanup registrado fora do lifecycle ativo.")
            self._cleanup.append(callback)

    def mark_running(self) -> None:
        with self._lock:
            if self._state is not RuntimeState.STARTING:
                raise RuntimeError("Runtime não está inicializando.")
            self._set_state(RuntimeState.RUNNING)

    def add_finalizer(self, callback: Callable[[], None]) -> None:
        """Registers work that runs after the final STOPPED publication."""
        with self._lock:
            self._finalizers.append(callback)

    def stop(self) -> tuple[Exception, ...]:
        with self._lock:
            if self._state is RuntimeState.STOPPED:
                return ()
            if self._state is RuntimeState.STOPPING:
                return ()
            self._set_state(RuntimeState.STOPPING)
            callbacks = tuple(reversed(self._cleanup))
            self._cleanup.clear()
        errors: list[Exception] = []
        for callback in callbacks:
            try:
                callback()
            except Exception as error:
                errors.append(error)
        with self._lock:
            self._set_state(RuntimeState.STOPPED)
            finalizers = tuple(reversed(self._finalizers))
            self._finalizers.clear()
        for callback in finalizers:
            try:
                callback()
            except Exception as error:
                errors.append(error)
        return tuple(errors)

    def _set_state(self, state: RuntimeState) -> None:
        self._state = state
        if self._on_state_change is not None:
            try:
                self._on_state_change(state)
            except Exception:
                # Observability must never prevent startup rollback or cleanup.
                pass
