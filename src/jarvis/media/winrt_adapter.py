from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from threading import Event
from typing import Any

from jarvis.media.models import (
    MediaCapabilities,
    MediaMetadata,
    MediaSnapshot,
    PlaybackState,
    SessionBinding,
)


class WinrtGsmtcAdapter:
    """PyWinRT adapter. Imports are lazy so disabled GSMTC has no dependency cost."""

    def available(self) -> bool:
        if os.name != "nt":
            return False
        try:
            self._api()
        except (ImportError, OSError):
            return False
        return True

    def discover_manager(self, cancel: Event) -> object:
        api = self._api()
        return self._await(api.request_async(), cancel)

    def current_session(self, manager: object, cancel: Event) -> object | None:
        self._check(cancel)
        return manager.get_current_session()  # type: ignore[attr-defined]

    def bind_session(self, session: object, cancel: Event) -> SessionBinding:
        self._check(cancel)
        source = str(session.source_app_user_model_id)  # type: ignore[attr-defined]
        state = self.playback_state(session, cancel)
        capabilities = self.capabilities(session, cancel)
        metadata = self.metadata(session, cancel)
        identity = f"{source}|{id(session)}"
        return SessionBinding(
            identity, session, MediaSnapshot(state, capabilities, metadata)
        )

    def same_session(self, expected: SessionBinding, current: object) -> bool:
        try:
            source = str(current.source_app_user_model_id)  # type: ignore[attr-defined]
            expected_source = expected.identity.split("|", 1)[0]
            return expected_source == source and expected.handle == current
        except (AttributeError, TypeError):
            return False

    def playback_state(self, session: object, cancel: Event) -> PlaybackState:
        self._check(cancel)
        info = session.get_playback_info()  # type: ignore[attr-defined]
        if info is None:
            return PlaybackState.CLOSED
        name = getattr(info.playback_status, "name", str(info.playback_status))
        return {
            "PLAYING": PlaybackState.PLAYING,
            "PAUSED": PlaybackState.PAUSED,
            "STOPPED": PlaybackState.STOPPED,
            "CLOSED": PlaybackState.CLOSED,
            "CHANGING": PlaybackState.CHANGING,
        }.get(str(name).upper().rsplit(".", 1)[-1], PlaybackState.UNKNOWN)

    def capabilities(self, session: object, cancel: Event) -> MediaCapabilities:
        self._check(cancel)
        info = session.get_playback_info()  # type: ignore[attr-defined]
        controls = None if info is None else info.controls
        return MediaCapabilities(
            play=bool(controls and controls.is_play_enabled),
            pause=bool(controls and controls.is_pause_enabled),
            next=bool(controls and controls.is_next_enabled),
            previous=bool(controls and controls.is_previous_enabled),
        )

    def metadata(self, session: object, cancel: Event) -> MediaMetadata:
        self._check(cancel)
        properties = self._await(
            session.try_get_media_properties_async(), cancel  # type: ignore[attr-defined]
        )
        self._check(cancel)
        timeline = session.get_timeline_properties()  # type: ignore[attr-defined]
        position = self._duration(getattr(timeline, "position", None))
        start = self._duration(getattr(timeline, "start_time", None))
        end = self._duration(getattr(timeline, "end_time", None))
        duration = end - start if end is not None and start is not None else end
        return MediaMetadata(
            source=str(session.source_app_user_model_id),  # type: ignore[attr-defined]
            title=self._optional(getattr(properties, "title", None)),
            artist=self._optional(getattr(properties, "artist", None)),
            position=position,
            duration=duration,
        )

    def execute(self, session: object, action: str, cancel: Event) -> bool:
        methods = {
            "play": "try_play_async",
            "pause": "try_pause_async",
            "next": "try_skip_next_async",
            "previous": "try_skip_previous_async",
        }
        try:
            method = methods[action]
        except KeyError as error:
            raise ValueError("Ação GSMTC inválida.") from error
        return bool(self._await(getattr(session, method)(), cancel))

    @staticmethod
    def _api() -> Any:
        from winrt.windows.media.control import (  # type: ignore[import-not-found]
            GlobalSystemMediaTransportControlsSessionManager,
        )
        return GlobalSystemMediaTransportControlsSessionManager

    @staticmethod
    def _await(operation: Any, cancel: Event) -> Any:
        WinrtGsmtcAdapter._check(cancel)
        async def wait_for_operation() -> Any:
            return await operation
        async def cancellable() -> Any:
            task = asyncio.create_task(wait_for_operation())
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=0.05)
                    if cancel.is_set():
                        native_cancel = getattr(operation, "cancel", None)
                        if callable(native_cancel):
                            native_cancel()
                        raise TimeoutError("Operação GSMTC cancelada.")
                return task.result()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        result = asyncio.run(cancellable())
        WinrtGsmtcAdapter._check(cancel)
        return result

    @staticmethod
    def _check(cancel: Event) -> None:
        if cancel.is_set():
            raise TimeoutError("Operação GSMTC cancelada.")

    @staticmethod
    def _optional(value: object) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    @staticmethod
    def _duration(value: object) -> timedelta | None:
        return value if isinstance(value, timedelta) else None
