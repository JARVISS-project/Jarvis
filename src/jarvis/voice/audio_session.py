"""Ownership serializado do único stream de microfone do modo voice."""

from contextlib import contextmanager
from threading import RLock
from typing import Iterator

from jarvis.activation.listener import WakeWordListener
from jarvis.audio.utterance import CaptureControl, UtteranceRecorder
from jarvis.tts.base import SpeechPlaybackHandle, TextToSpeech
from jarvis.voice.ptt import PttController, PttState


class VoiceAudioSession:
    def __init__(self, listener: WakeWordListener) -> None:
        self.listener = listener
        self.audio_input = listener.audio_input
        self._lock = RLock()
        self._owner: str | None = None
        self.ptt = PttController()

    @property
    def owner(self) -> str | None:
        return self._owner

    @contextmanager
    def ownership(self, owner: str) -> Iterator[None]:
        with self._lock:
            previous = self._owner
            self._owner = owner
            try:
                yield
            finally:
                self._owner = previous

    def capture(
        self,
        owner: str,
        recorder: UtteranceRecorder,
        control: CaptureControl | None = None,
    ) -> list[int]:
        with self.ownership(owner):
            if self.ptt.state is PttState.CANCELLED:
                return []
            self.ptt.begin_capture()
            try:
                self.listener.start()
                if isinstance(recorder, UtteranceRecorder):
                    return recorder.record(control=control or self.ptt)
                return recorder.record()
            finally:
                self.ptt.finish_capture()

    def wait_playback(self, playback: SpeechPlaybackHandle) -> None:
        """The owner stops and joins playback before handing audio to capture."""
        try:
            while not playback.wait(0.02):
                if self.ptt.pending or self.ptt.stop_requested:
                    playback.stop()
                    playback.wait()
                    break
        except Exception:
            if not playback.finished:
                self.ptt.close()
                self.listener.close()
            raise

    def speak_then_capture(
        self,
        prompt: str,
        text_to_speech: TextToSpeech,
        recorder: UtteranceRecorder,
    ) -> list[int]:
        with self.ownership("confirmation"):
            self.speak(text_to_speech, prompt)
            return self.capture("confirmation", recorder)

    def speak(self, text_to_speech: TextToSpeech, text: str) -> None:
        with self.ownership("playback"):
            self.listener.stop()
            if self.ptt.pending or self.ptt.state is PttState.CANCELLED:
                return
            self.wait_playback(text_to_speech.speak_async(text))

    def stop_stream(self) -> None:
        with self._lock:
            self.listener.stop()

    def start_stream(self) -> None:
        with self._lock:
            self.listener.start()
