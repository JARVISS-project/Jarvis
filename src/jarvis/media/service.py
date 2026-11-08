from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from hashlib import sha256
from threading import Event, Lock
from typing import Callable, TypeVar

from jarvis.media.adapter import GsmtcAdapter
from jarvis.media.models import (
    MediaSnapshot,
    PlaybackState,
    PreparedMediaOperation,
    SessionBinding,
)


T = TypeVar("T")
_ACTIONS = frozenset({"play", "pause", "next", "previous"})


class GsmtcMediaService:
    def __init__(self, adapter: GsmtcAdapter, *, timeout: float = 2.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout GSMTC deve ser positivo")
        self.adapter = adapter
        self.timeout = timeout
        self._effect_lock = Lock()

    def available(self) -> bool:
        return self.adapter.available()

    def snapshot(self) -> MediaSnapshot:
        binding = self._current_binding()
        return binding.snapshot

    def prepare(self, action: str) -> PreparedMediaOperation:
        if action not in _ACTIONS:
            raise ValueError("Ação de mídia inválida.")
        binding = self._current_binding()
        state = binding.snapshot.state
        if state in {PlaybackState.UNKNOWN, PlaybackState.UNAVAILABLE,
                     PlaybackState.CLOSED, PlaybackState.CHANGING}:
            raise RuntimeError(f"Estado de mídia não autoriza ação: {state.value}.")
        return PreparedMediaOperation(action, binding)

    def binding_digest(self, prepared: PreparedMediaOperation) -> str:
        value = f"{prepared.action}|{prepared.session.identity}"
        return sha256(value.encode("utf-8")).hexdigest()

    def revalidate(self, prepared: PreparedMediaOperation) -> PlaybackState:
        manager = self._call(self.adapter.discover_manager)
        current = self._call(lambda cancel: self.adapter.current_session(manager, cancel))
        if current is None or not self.adapter.same_session(prepared.session, current):
            raise PermissionError("A sessão de mídia mudou após o preparo.")
        state = self._call(lambda cancel: self.adapter.playback_state(current, cancel))
        capabilities = self._call(lambda cancel: self.adapter.capabilities(current, cancel))
        if state in {PlaybackState.UNKNOWN, PlaybackState.UNAVAILABLE,
                     PlaybackState.CLOSED, PlaybackState.CHANGING}:
            raise PermissionError("A sessão de mídia não possui estado seguro.")
        already_satisfied = (
            prepared.action == "play" and state is PlaybackState.PLAYING
        ) or (
            prepared.action == "pause" and state is PlaybackState.PAUSED
        )
        if not already_satisfied and not capabilities.permits(prepared.action):
            raise PermissionError("A sessão não declara suporte à ação solicitada.")
        return state

    def execute(self, prepared: PreparedMediaOperation) -> str:
        with self._effect_lock:
            before = self.revalidate(prepared)
            if prepared.action == "play" and before is PlaybackState.PLAYING:
                return "A mídia já está reproduzindo."
            if prepared.action == "pause" and before is PlaybackState.PAUSED:
                return "A mídia já está pausada."
            succeeded = self._call(
                lambda cancel: self.adapter.execute(
                    prepared.session.handle, prepared.action, cancel
                )
            )
            if succeeded is not True:
                raise RuntimeError("O Windows recusou o comando de mídia.")
            after = self._verified_state(prepared)
            if prepared.action in {"play", "pause"}:
                expected = (PlaybackState.PLAYING if prepared.action == "play"
                            else PlaybackState.PAUSED)
                if after is not expected:
                    raise RuntimeError(
                        f"Estado posterior divergente: {after.value}."
                    )
            return {
                "play": "Reprodução iniciada e verificada.",
                "pause": "Mídia pausada e verificada.",
                "next": "Próxima faixa solicitada uma vez.",
                "previous": "Faixa anterior solicitada uma vez.",
            }[prepared.action]

    def describe(self) -> str:
        snapshot = self.snapshot()
        metadata = snapshot.metadata
        details = [f"Estado: {snapshot.state.value}."]
        if metadata.title:
            details.append(f"Título: {metadata.title}.")
        if metadata.artist:
            details.append(f"Artista: {metadata.artist}.")
        if metadata.source:
            details.append(f"Origem: {metadata.source}.")
        if metadata.position is not None and metadata.duration is not None:
            details.append(
                f"Posição: {int(metadata.position.total_seconds())} de "
                f"{int(metadata.duration.total_seconds())} segundos."
            )
        return " ".join(details)

    def _current_binding(self) -> SessionBinding:
        if not self.available():
            raise RuntimeError("GSMTC indisponível.")
        manager = self._call(self.adapter.discover_manager)
        session = self._call(lambda cancel: self.adapter.current_session(manager, cancel))
        if session is None:
            raise RuntimeError("Nenhuma sessão de mídia atual.")
        binding = self._call(lambda cancel: self.adapter.bind_session(session, cancel))
        if binding.snapshot.state in {PlaybackState.UNKNOWN, PlaybackState.UNAVAILABLE}:
            raise RuntimeError("Estado atual de mídia desconhecido.")
        return binding

    def _verified_state(self, prepared: PreparedMediaOperation) -> PlaybackState:
        manager = self._call(self.adapter.discover_manager)
        current = self._call(lambda cancel: self.adapter.current_session(manager, cancel))
        if current is None or not self.adapter.same_session(prepared.session, current):
            raise PermissionError("A sessão de mídia mudou durante a ação.")
        return self._call(lambda cancel: self.adapter.playback_state(current, cancel))

    def _call(self, operation: Callable[[Event], T]) -> T:
        cancel = Event()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-gsmtc")
        future = executor.submit(operation, cancel)
        try:
            return future.result(timeout=self.timeout)
        except FutureTimeout as error:
            cancel.set()
            future.cancel()
            raise TimeoutError("Operação GSMTC excedeu o timeout.") from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
