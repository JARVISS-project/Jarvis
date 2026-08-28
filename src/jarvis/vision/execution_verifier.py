"""Visual change signal bound to authorized Desktop invocations."""

from jarvis.vision.service import ScreenChange, VisionService


class VisionToolExecutionVerifier:
    SUPPORTED_TOOLS = frozenset(
        {
            "desktop_focus_window",
            "desktop_open_application",
            "desktop_move_pointer_to_window",
            "desktop_press_escape",
            "desktop_close_window",
        }
    )

    def __init__(self, service: VisionService) -> None:
        self.service = service

    def before(self, tool_name: str, target: str):  # type: ignore[no-untyped-def]
        if tool_name not in self.SUPPORTED_TOOLS:
            return None
        return self.service.begin_verification(f"{tool_name}:{target}")

    def after(
        self,
        token,  # type: ignore[no-untyped-def]
        tool_name: str,
        target: str,
    ) -> str | None:
        if token is None or tool_name not in self.SUPPORTED_TOOLS:
            return None
        result = self.service.finish_verification(
            token,
            f"{tool_name}:{target}",
        )
        if result.status is ScreenChange.BASELINE:
            return None
        return result.status.value

    def cancel(self, token) -> None:  # type: ignore[no-untyped-def]
        if token is not None:
            self.service.cancel_verification(token)
