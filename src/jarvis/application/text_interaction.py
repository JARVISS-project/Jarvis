"""Ephemeral local text turns over the existing UI channel, without Tool authority."""
from __future__ import annotations

from collections.abc import Callable
from queue import Queue
import re
from threading import RLock, Thread
from typing import Any
from uuid import uuid4

MAX_TEXT_BYTES = 16384
_ID = re.compile(r"^[a-f0-9]{32}$")
_CONTEXT_ID = re.compile(r"^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$")


def valid_text_command(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    command = payload.get("command")
    keys = {"command", "generation", "interaction"}
    if command == "text_submit":
        keys.add("text")
        if not isinstance(payload.get("text"), str) or not 0 < len(payload["text"].strip()) <= 2000:
            return False
    elif command == "text_confirm":
        keys.update({"session", "challenge"})
        if not all(isinstance(payload.get(k), str) and _CONTEXT_ID.fullmatch(payload[k])
                   for k in ("session", "challenge")):
            return False
    elif command != "text_cancel":
        return False
    return set(payload) == keys and all(
        isinstance(payload.get(k), str) and _ID.fullmatch(payload[k])
        for k in ("generation", "interaction")
    )


class TextEventBus:
    def __init__(self) -> None:
        self._lock = RLock()
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self.snapshot: dict[str, Any] = {}

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.snapshot = dict(event)
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            subscriber(dict(event))

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)
            callback(dict(self.snapshot))
        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)
        return unsubscribe


class TextInteractionController:
    """One bounded worker; Harness serializes text with voice/console turns."""
    def __init__(self, harness: Any, speech=None) -> None:
        self.harness = harness
        self.speech = speech
        self.generation = uuid4().hex
        self._lock = RLock()
        self._queue: Queue[Any] = Queue(maxsize=1)
        self._active: tuple[object, str] | None = None
        self._pending: tuple[object, str, str, str] | None = None
        self._cancelled = False
        self._closed = False
        # IDs are monotonically fenced by a bounded lifetime: never evict/replay.
        self._seen: set[str] = set()
        self._thread = Thread(target=self._run, name="jarvis-ui-text", daemon=False)
        self._thread.start()

    def hello(self) -> dict[str, Any]:
        return {"type": "text", "generation": self.generation, "status": "ready"}

    def handle(self, owner: object, payload: dict[str, Any], reply: Callable) -> None:
        with self._lock:
            event = {"type": "text", "generation": self.generation,
                     "interaction": payload.get("interaction", ""), "status": "error"}
            if self._closed or not valid_text_command(payload) or payload["generation"] != self.generation:
                reply({**event, "text": "Interação inválida ou runtime reiniciado."})
                return
            key = (owner, payload["interaction"])
            command = payload["command"]
            if command == "text_cancel":
                if self._active == key:
                    self._cancelled = True
                    if self.speech is not None:
                        self.speech.stop()
                    cancel_work = getattr(self.harness, 'cancel_active_work', None)
                    if callable(cancel_work):
                        cancel_work()
                    reply({**event, "status": "busy", "text": "Cancelamento solicitado; trabalho em andamento será drenado."})
                elif self._pending is not None and self._pending[:2] == key:
                    self._queue.put_nowait((owner, payload, reply))
                    self._active = key
                    self._cancelled = True
                else:
                    reply({**event, "text": "Nenhuma interação correspondente para cancelar."})
                return
            if self._active is not None:
                reply({**event, "text": "Runtime ocupado; envio não aceito."})
                return
            if command == "text_confirm":
                if self._pending != (owner, payload["interaction"], payload["session"], payload["challenge"]):
                    reply({**event, "text": "Confirmação antiga ou de outra interação."})
                    return
            elif self._pending is not None or payload["interaction"] in self._seen or len(self._seen) >= 4096:
                reply({**event, "text": "Envio duplicado ou confirmação pendente; reinicie a conexão se necessário."})
                return
            self._seen.add(payload["interaction"])
            self._active = key
            self._cancelled = False
            self._queue.put_nowait((owner, dict(payload), reply))
            reply({**event, "status": "busy"})

    def disconnect(self, owner: object) -> None:
        with self._lock:
            if self._active is not None and self._active[0] == owner:
                self._cancelled = True
                if self.speech is not None:
                    self.speech.stop()
                cancel_work = getattr(self.harness, 'cancel_active_work', None)
                if callable(cancel_work):
                    cancel_work()
            if self._pending is not None and self._pending[0] == owner and self._active is None:
                payload = {"command": "text_cancel", "generation": self.generation,
                           "interaction": self._pending[1]}
                self.handle(owner, payload, lambda _event: None)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            owner, payload, reply = item
            event = {"type": "text", "generation": self.generation,
                     "interaction": payload["interaction"]}
            routines = getattr(self.harness, 'routines', None)
            if routines is not None:
                routines.progress = lambda message: reply({**event, 'status': 'busy', 'text': message})
            delegation = getattr(self.harness, 'delegation', None)
            if delegation is not None:
                delegation.progress = lambda message: reply({**event, 'status': 'busy', 'text': message})
            try:
                with self.harness._turn_lock:
                    context = self.harness.current_confirmation_context()
                    with self._lock:
                        cancelled = self._cancelled
                        pending = self._pending
                    if cancelled:
                        if pending is not None and context == pending[2:]:
                            self.harness.cancel_pending_confirmation()
                        response, status = "Interação cancelada. Efeitos já executados não são desfeitos.", "cancelled"
                    else:
                        if payload["command"] == "text_confirm":
                            if context != (payload["session"], payload["challenge"]):
                                raise PermissionError
                            response = self.harness.processar_mensagem(
                                "sim", response_mode="text", confirmation_session_id=context[0],
                                confirmation_challenge_id=context[1])
                        else:
                            if context is not None:
                                raise PermissionError
                            response = self.harness.processar_mensagem(payload["text"], response_mode="text")
                        status = "done"
                    context = self.harness.current_confirmation_context()
                    with self._lock:
                        if self._cancelled:
                            if context is not None:
                                self.harness.cancel_pending_confirmation()
                            context = None
                            status = "cancelled"
                            response = "Interação cancelada. Efeitos já executados não são desfeitos."
                        self._pending = (owner, payload["interaction"], *context) if context else None
                    if context:
                        event.update(session=context[0], challenge=context[1])
                        status = "confirmation"
                    event.update(status=status, text=str(response))
            except Exception:
                event.update(status="cancelled" if self._cancelled else "error",
                             text="Interação cancelada." if self._cancelled else
                             "Falha ao processar a interação; nenhum sucesso confirmado.")
                with self.harness._turn_lock:
                    if self._pending and self.harness.current_confirmation_context() == self._pending[2:]:
                        self.harness.cancel_pending_confirmation()
                self._pending = None
            if (self.speech is not None and event.get("status") == "done"
                    and self.speech.config.tts_enabled and not self._cancelled):
                # Emit the final text before synthesis; Qt remains interactive.
                reply({**event, "status": "speaking"})
                try:
                    from jarvis.tts.text import prepare_text_for_speech
                    playback = self.speech.speak_ui_async(prepare_text_for_speech(event["text"]))
                    if not playback.wait(120):
                        playback.stop()
                        raise TimeoutError
                    event["text"] = ""
                    if self._cancelled:
                        event.update(status="cancelled", text="Fala interrompida.")
                except Exception:
                    event.update(status="cancelled" if self._cancelled else "done",
                                 text="Fala interrompida." if self._cancelled else
                                 "Resposta disponível acima; saída de voz indisponível.")
            with self._lock:
                if routines is not None:
                    routines.progress = lambda message: None
                if delegation is not None:
                    delegation.progress = lambda message: None
                self._active = None
                reply(event)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancelled = True
            if self.speech is not None:
                self.speech.stop()
            cancel_work = getattr(self.harness, 'cancel_active_work', None)
            if callable(cancel_work):
                cancel_work()
        self._queue.put(None)
        self._thread.join()
        with self.harness._turn_lock:
            if self._pending and self.harness.current_confirmation_context() == self._pending[2:]:
                self.harness.cancel_pending_confirmation()
        self._pending = None
