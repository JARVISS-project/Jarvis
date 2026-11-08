"""Visual change signal bound to authorized Desktop invocations."""

from jarvis.desktop.models import PreparedDesktopWindow
from jarvis.vision.models import CaptureRegion
from jarvis.vision.service import ScreenChange, VisionService


class VisionToolExecutionVerifier:
    SUPPORTED_TOOLS = frozenset(
        {
            "desktop_focus_window",
            "desktop_move_pointer_to_window",
            "desktop_press_escape",
            "desktop_close_window",
        }
    )

    def __init__(self, service: VisionService) -> None:
        self.service = service

    def before(
        self,
        tool_name: str,
        target: str,
        context: object | None = None,
    ):  # type: ignore[no-untyped-def]
        if (
            tool_name not in self.SUPPORTED_TOOLS
            or not isinstance(context, PreparedDesktopWindow)
            or context.rectangle is None
        ):
            return None
        rectangle = context.rectangle
        return self.service.begin_verification(
            self._identity(tool_name, context),
            CaptureRegion(
                rectangle.left,
                rectangle.top,
                rectangle.right,
                rectangle.bottom,
            ),
        )

    def after(
        self,
        token,  # type: ignore[no-untyped-def]
        tool_name: str,
        target: str,
        context: object | None = None,
    ) -> str | None:
        if (
            token is None
            or tool_name not in self.SUPPORTED_TOOLS
            or not isinstance(context, PreparedDesktopWindow)
        ):
            return None
        result = self.service.finish_verification(
            token,
            self._identity(tool_name, context),
        )
        if result.status is ScreenChange.BASELINE:
            return None
        return result.status.value

    def cancel(self, token) -> None:  # type: ignore[no-untyped-def]
        if token is not None:
            self.service.cancel_verification(token)

    @staticmethod
    def _identity(
        tool_name: str,
        context: PreparedDesktopWindow,
    ) -> str:
        return f"{tool_name}:{context.visual_identity()}"
