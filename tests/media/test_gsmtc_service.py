from __future__ import annotations

from datetime import timedelta
import logging
import threading
import time

import pytest

from jarvis.media.models import (
    MediaCapabilities,
    MediaMetadata,
    MediaSnapshot,
    PlaybackState,
    SessionBinding,
)
from jarvis.media.service import GsmtcMediaService


class FakeGsmtcAdapter:
    def __init__(self, state=PlaybackState.PAUSED) -> None:
        self.enabled = True
        self.manager = object()
        self.session = object()
        self.state = state
        self.controls = MediaCapabilities(True, True, True, True)
        self.properties = MediaMetadata(
            "SpotifyAB.SpotifyMusic", "Canção çã 🎵", "Artista Único",
            timedelta(seconds=12), timedelta(seconds=240),
        )
        self.calls: list[str] = []
        self.result = True
        self.error: Exception | None = None
        self.delay = 0.0
        self.after_state: PlaybackState | None = None
        self.swap_before_effect = False

    def available(self): return self.enabled
    def discover_manager(self, cancel): return self.manager
    def current_session(self, manager, cancel): return self.session
    def bind_session(self, session, cancel):
        return SessionBinding(
            f"source|{id(session)}", session,
            MediaSnapshot(self.state, self.controls, self.properties),
        )
    def same_session(self, expected, current):
        return expected.handle is current
    def playback_state(self, session, cancel): return self.state
    def capabilities(self, session, cancel): return self.controls
    def metadata(self, session, cancel): return self.properties
    def execute(self, session, action, cancel):
        self.calls.append(action)
        if self.swap_before_effect:
            self.session = object()
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        if self.after_state is not None:
            self.state = self.after_state
        return self.result


def service(adapter=None, timeout=.2):
    return GsmtcMediaService(adapter or FakeGsmtcAdapter(), timeout=timeout)


def test_no_session_and_unavailable_fail_closed():
    adapter = FakeGsmtcAdapter(); adapter.session = None
    with pytest.raises(RuntimeError, match="Nenhuma sessão"):
        service(adapter).snapshot()
    adapter.enabled = False
    with pytest.raises(RuntimeError, match="indisponível"):
        service(adapter).snapshot()


@pytest.mark.parametrize("state", [
    PlaybackState.PLAYING, PlaybackState.PAUSED, PlaybackState.STOPPED,
])
def test_canonical_states_are_reported(state):
    assert service(FakeGsmtcAdapter(state)).snapshot().state is state


@pytest.mark.parametrize("state", [
    PlaybackState.UNKNOWN, PlaybackState.UNAVAILABLE, PlaybackState.CLOSED,
    PlaybackState.CHANGING,
])
def test_unknown_states_never_authorize(state):
    adapter = FakeGsmtcAdapter(state)
    with pytest.raises(RuntimeError, match="desconhecido|não autoriza"):
        service(adapter).prepare("play")
    assert adapter.calls == []


def test_play_never_pauses_and_repeated_play_is_idempotent():
    adapter = FakeGsmtcAdapter(PlaybackState.PAUSED)
    adapter.after_state = PlaybackState.PLAYING
    operation = service(adapter).prepare("play")
    assert "iniciada" in service(adapter).execute(operation)
    assert adapter.calls == ["play"]
    second = service(adapter).prepare("play")
    assert "já está" in service(adapter).execute(second)
    assert adapter.calls == ["play"]


def test_pause_never_plays_and_repeated_pause_is_idempotent():
    adapter = FakeGsmtcAdapter(PlaybackState.PLAYING)
    adapter.after_state = PlaybackState.PAUSED
    media = service(adapter)
    assert "pausada" in media.execute(media.prepare("pause"))
    assert "já está" in media.execute(media.prepare("pause"))
    assert adapter.calls == ["pause"]


@pytest.mark.parametrize("action", ["next", "previous"])
def test_skip_is_attempted_at_most_once(action):
    adapter = FakeGsmtcAdapter(PlaybackState.PLAYING)
    media = service(adapter)
    assert "uma vez" in media.execute(media.prepare(action))
    assert adapter.calls == [action]


def test_missing_capability_and_false_result_fail():
    adapter = FakeGsmtcAdapter()
    adapter.controls = MediaCapabilities(play=True)
    media = service(adapter)
    operation = media.prepare("next")
    with pytest.raises(PermissionError, match="suporte"):
        media.execute(operation)
    assert adapter.calls == []
    adapter.controls = MediaCapabilities(True, True, True, True)
    adapter.result = False
    with pytest.raises(RuntimeError, match="recusou"):
        media.execute(media.prepare("next"))
    assert adapter.calls == ["next"]


def test_session_change_before_and_during_effect_fails_closed():
    adapter = FakeGsmtcAdapter()
    media = service(adapter)
    operation = media.prepare("next")
    adapter.session = object()
    with pytest.raises(PermissionError, match="mudou"):
        media.execute(operation)
    assert adapter.calls == []
    adapter = FakeGsmtcAdapter(); adapter.after_state = PlaybackState.PLAYING
    adapter.swap_before_effect = True
    media = service(adapter)
    with pytest.raises(PermissionError, match="durante"):
        media.execute(media.prepare("play"))
    assert adapter.calls == ["play"]


def test_false_timeout_winrt_exception_and_late_response_do_not_retry():
    adapter = FakeGsmtcAdapter(); adapter.error = OSError("winrt")
    media = service(adapter)
    with pytest.raises(OSError, match="winrt"):
        media.execute(media.prepare("next"))
    assert adapter.calls == ["next"]
    adapter = FakeGsmtcAdapter(); adapter.delay = .08
    media = service(adapter, timeout=.01)
    with pytest.raises(TimeoutError):
        media.execute(media.prepare("next"))
    time.sleep(.1)
    assert adapter.calls == ["next"]


def test_post_playback_state_must_converge():
    adapter = FakeGsmtcAdapter(PlaybackState.PAUSED)
    with pytest.raises(RuntimeError, match="divergente"):
        service(adapter).execute(service(adapter).prepare("play"))
    assert adapter.calls == ["play"]


def test_metadata_missing_unicode_and_log_privacy(caplog):
    adapter = FakeGsmtcAdapter()
    text = service(adapter).describe()
    assert "Canção çã 🎵" in text and "Artista Único" in text
    adapter.properties = MediaMetadata()
    assert service(adapter).describe() == "Estado: paused."
    logging.getLogger("jarvis").info("capability=current_media result=success")
    assert "Canção" not in caplog.text and "Artista" not in caplog.text


def test_two_concurrent_effects_are_serialized_without_hidden_retry():
    adapter = FakeGsmtcAdapter(PlaybackState.PLAYING)
    adapter.delay = .02
    media = service(adapter)
    prepared = media.prepare("next")
    errors = []
    threads = [threading.Thread(target=lambda: (
        media.execute(prepared)
    )) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert errors == [] and adapter.calls == ["next", "next"]
