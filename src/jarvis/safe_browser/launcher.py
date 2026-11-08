from __future__ import annotations

from typing import Protocol

from jarvis.safe_browser.models import ValidatedUri


class BrowserLauncher(Protocol):
    def association_token(self) -> str | None: ...
    def launch(self, request: ValidatedUri) -> bool: ...
