"""Porta mínima entre o Remote Gateway e o Core do JARVIS."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.harness import Harness


@dataclass(frozen=True, slots=True)
class RemoteConfirmation:
    session_id: str
    challenge_id: str
    action: str
    target: str
    risk: str
    prompt_text: str
    expires_at: float
    reminder_count: int


class RemoteCorePort(Protocol):
    """Única autoridade que o Gateway pode invocar no Core."""

    def process_chat(self, text: str) -> str: ...

    def process_confirmation(
        self,
        *,
        confirmed: bool,
        session_id: str,
        challenge_id: str,
    ) -> str: ...

    def current_confirmation(self) -> RemoteConfirmation | None: ...


class HarnessRemoteCorePort:
    """Adapta o Harness sem expor Tools, executor ou grants ao Gateway."""

    def __init__(self, harness: Harness) -> None:
        self.__harness = harness
        self.__lock = threading.RLock()

    def process_chat(self, text: str) -> str:
        with self.__lock:
            return self.__harness.processar_mensagem(text, response_mode="text")

    def process_confirmation(
        self,
        *,
        confirmed: bool,
        session_id: str,
        challenge_id: str,
    ) -> str:
        with self.__lock:
            return self.__harness.processar_mensagem(
                "sim" if confirmed else "cancelar",
                response_mode="text",
                confirmation_session_id=session_id,
                confirmation_challenge_id=challenge_id,
            )

    def current_confirmation(self) -> RemoteConfirmation | None:
        with self.__lock:
            session = self.__harness.confirmation_sessions.current()
            if session is None:
                return None
            invocation = session.invocation
            return RemoteConfirmation(
                session_id=session.id,
                challenge_id=session.challenge_id,
                action=invocation.tool_name,
                target=invocation.target,
                risk=invocation.risk.name,
                prompt_text=session.prompt_text,
                expires_at=session.expires_at,
                reminder_count=session.reminder_count,
            )
