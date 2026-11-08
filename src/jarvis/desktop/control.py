"""Kill switch independente do LLM para ações de controle do desktop."""

from threading import Event, RLock


class DesktopKillSwitch:
    """Bloqueia novas ações de controle assim que acionado.

    O estado é intencionalmente fail-closed: depois de acionado, apenas código
    local explícito pode resetá-lo. Nenhuma Tool recebe uma operação de reset.
    """

    def __init__(self) -> None:
        self._tripped = Event()
        self._lock = RLock()
        self._reason: str | None = None

    @property
    def is_tripped(self) -> bool:
        return self._tripped.is_set()

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def trip(self, reason: str = "interrupção de emergência") -> None:
        with self._lock:
            self._reason = reason.strip() or "interrupção de emergência"
            self._tripped.set()

    def reset_local(self) -> None:
        """Rearma localmente o controle; não deve ser exposto como Tool."""
        with self._lock:
            self._reason = None
            self._tripped.clear()

    def require_available(self) -> None:
        if self.is_tripped:
            reason = self.reason or "interrupção de emergência"
            raise PermissionError(
                f"Controle de desktop bloqueado pelo kill switch: {reason}."
            )
