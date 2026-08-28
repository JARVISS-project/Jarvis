"""Optional post-execution verification contract for ToolExecutor."""

from typing import Any, Protocol


class ToolExecutionVerifier(Protocol):
    def before(self, tool_name: str, target: str) -> Any | None: ...

    def after(self, token: Any, tool_name: str, target: str) -> str | None: ...

    def cancel(self, token: Any) -> None: ...
