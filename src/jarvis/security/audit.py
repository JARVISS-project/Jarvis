from datetime import datetime
from pathlib import Path
from collections.abc import Callable


class AuditLogger:
    def __init__(
        self,
        log_file: Path,
        telemetry_callback: Callable[[bool], None] | None = None,
    ) -> None:
        self.log_file = log_file
        self.telemetry_callback = telemetry_callback
        self.log_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def log(
        self,
        tool_name: str,
        target: str,
        authorized: bool,
        status: str,
    ) -> None:
        timestamp = datetime.now().isoformat(
            timespec="seconds"
        )

        entry = (
            f"[{timestamp}] "
            f"tool={tool_name} "
            f"target={target} "
            f"authorized={authorized} "
            f"status={status}\n"
        )

        try:
            with self.log_file.open(
                "a",
                encoding="utf-8",
            ) as file:
                file.write(entry)
        except Exception:
            self._notify_telemetry(False)
            raise
        self._notify_telemetry(True)

    def _notify_telemetry(self, succeeded: bool) -> None:
        callback = self.telemetry_callback
        if callback is None:
            return
        try:
            callback(succeeded)
        except Exception:
            return
