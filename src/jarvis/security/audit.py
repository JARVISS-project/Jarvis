from datetime import datetime
from pathlib import Path


class AuditLogger:
    def __init__(self, log_file: Path) -> None:
        self.log_file = log_file
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

        with self.log_file.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(entry)