"""Ponto de controle para interrupção futura de playback."""

from jarvis.runtime.cancellation import CancellationController
from jarvis.tts.base import TextToSpeech


class SpeechPlaybackController:
    def __init__(
        self,
        text_to_speech: TextToSpeech,
        cancellation: CancellationController,
    ) -> None:
        self.text_to_speech = text_to_speech
        self.cancellation = cancellation

    def stop(self) -> None:
        self.cancellation.request_cancel()
        self.text_to_speech.stop()
