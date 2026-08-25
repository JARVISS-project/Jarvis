"""Ownership serializado do único stream de microfone do modo voice."""

from contextlib import contextmanager
from threading import RLock
from typing import Iterator

from jarvis.activation.listener import WakeWordListener
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.tts.base import TextToSpeech


class VoiceAudioSession:
    def __init__(self, listener: WakeWordListener) -> None:
        self.listener = listener
        self.audio_input = listener.audio_input
        self._lock = RLock()
        self._owner: str | None = None

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
    ) -> list[int]:
        with self.ownership(owner):
            self.listener.start()
            return recorder.record()

    def speak_then_capture(
        self,
        prompt: str,
        text_to_speech: TextToSpeech,
        recorder: UtteranceRecorder,
    ) -> list[int]:
        with self.ownership("confirmation"):
            self.listener.stop()
            text_to_speech.speak(prompt)
            self.listener.start()
            return recorder.record()

    def stop_stream(self) -> None:
        with self._lock:
            self.listener.stop()

    def start_stream(self) -> None:
        with self._lock:
            self.listener.start()
