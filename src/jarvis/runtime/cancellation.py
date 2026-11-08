"""Cancelamento cooperativo e reconhecimento determinístico."""

import re
from threading import Event

from jarvis.security.confirmation_intent import ConfirmationInterpreter


class CancellationController:
    def __init__(self) -> None:
        self._cancelled = Event()

    def request_cancel(self) -> None:
        self._cancelled.set()

    def clear(self) -> None:
        self._cancelled.clear()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()


class CancellationInterpreter:
    PATTERNS = tuple(
        re.compile(pattern)
        for pattern in (
            r"(?:pode\s+)?cancel(?:a|ar|e)(?:\s+isso)?",
            r"deixa(?:\s+isso)?\s+pra\s+la",
            r"(?:nao\s*,?\s*)?pode\s+deixar",
            r"nao\s+(?:faca|precisa)(?:\s+isso)?",
            r"esquece(?:\s+isso)?",
            r"(?:pode\s+)?parar",
            r"pare(?:\s+agora)?",
            r"para(?:\s+agora)?",
            r"aborta(?:\s+isso)?",
        )
    )

    def is_cancellation(self, text: str) -> bool:
        normalized = ConfirmationInterpreter.normalize(text)
        return any(pattern.fullmatch(normalized) for pattern in self.PATTERNS)
