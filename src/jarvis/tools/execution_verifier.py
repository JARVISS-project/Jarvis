"""Optional post-execution verification contract for ToolExecutor."""

from typing import Any, Protocol


class ToolExecutionVerifier(Protocol):
    def before(
        self,
        tool_name: str,
        target: str,
        context: object | None = None,
    ) -> Any | None: ...

    def after(
        self,
        token: Any,
        tool_name: str,
        target: str,
        context: object | None = None,
    ) -> str | None: ...

    def cancel(self, token: Any) -> None: ...
