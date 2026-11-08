"""Estado local mínimo do runtime e solicitação cooperativa de stop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import time

from jarvis.runtime.lifecycle import RuntimeCapabilities, RuntimeState


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    state: str
    pid: int
    instance_id: str
    started_at: float
    uptime_seconds: int
    capabilities: tuple[str, ...]
    remote_available: bool
    voice_available: bool
    ui_available: bool
    last_startup_error: str | None = None


class RuntimeHealthStore:
    def __init__(self, status_path: Path, stop_path: Path | None = None) -> None:
        self.status_path = status_path
        self.stop_path = stop_path or status_path.with_name("stop-request.json")

    def publish(
        self,
        *,
        state: RuntimeState,
        instance_id: str,
        started_at: float,
        capabilities: RuntimeCapabilities,
        last_startup_error: str | None = None,
    ) -> RuntimeHealth:
        health = RuntimeHealth(
            state=state.value,
            pid=os.getpid(),
            instance_id=instance_id,
            started_at=started_at,
            uptime_seconds=max(0, int(time.time() - started_at)),
            capabilities=capabilities.enabled(),
            remote_available=state is RuntimeState.RUNNING and capabilities.remote,
            voice_available=state is RuntimeState.RUNNING and capabilities.voice,
            ui_available=state is RuntimeState.RUNNING and capabilities.ui,
            last_startup_error=last_startup_error,
        )
        self._write_atomic(self.status_path, asdict(health))
        return health

    def read(self) -> RuntimeHealth | None:
        try:
            value = json.loads(self.status_path.read_text(encoding="utf-8"))
            return RuntimeHealth(
                state=str(value["state"]),
                pid=int(value["pid"]),
                instance_id=str(value["instance_id"]),
                started_at=float(value["started_at"]),
                uptime_seconds=int(value["uptime_seconds"]),
                capabilities=tuple(value.get("capabilities", ())),
                remote_available=bool(value["remote_available"]),
                voice_available=bool(value["voice_available"]),
                ui_available=bool(value["ui_available"]),
                last_startup_error=value.get("last_startup_error"),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def request_stop(self, instance_id: str) -> None:
        self._write_atomic(
            self.stop_path,
            {"instance_id": instance_id, "requested_at": time.time()},
        )

    def stop_requested(self, instance_id: str) -> bool:
        try:
            value = json.loads(self.stop_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        return value.get("instance_id") == instance_id

    def clear_stop_request(self) -> None:
        try:
            self.stop_path.unlink()
        except FileNotFoundError:
            pass

    def remove_status(self, instance_id: str) -> None:
        current = self.read()
        if current is None or current.instance_id != instance_id:
            return
        try:
            self.status_path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _write_atomic(path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
        temporary.replace(path)
