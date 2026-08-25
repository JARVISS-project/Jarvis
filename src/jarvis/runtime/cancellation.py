"""Cancelamento cooperativo e reconhecimento determinístico."""

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
    PHRASES = (
        "deixa pra la", "nao faca", "nao precisa", "pode deixar",
        "esquece isso",
        "cancelar", "cancela", "cancele", "parar", "pare",
        "deixa", "esquece", "aborta",
    )
    EXACT_STOP_PHRASES = frozenset({"para", "para agora", "pode parar"})

    def is_cancellation(self, text: str) -> bool:
        normalized = ConfirmationInterpreter.normalize(text)
        if normalized in self.EXACT_STOP_PHRASES:
            return True
        padded = f" {normalized} "
        return any(f" {phrase} " in padded for phrase in self.PHRASES)
