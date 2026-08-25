from collections.abc import Callable
import time
from typing import Protocol

from jarvis.activation.listener import WakeWordListener
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.speech.base import SpeechToText
from jarvis.tts.base import TextToSpeech
from jarvis.tts.text import prepare_text_for_speech


class _Harness(Protocol):
    def processar_mensagem(
        self,
        mensagem: str,
        manage_runtime: bool = True,
    ) -> str: ...


class VoicePipeline:
    def __init__(
        self,
        listener: WakeWordListener,
        recorder: UtteranceRecorder,
        speech_to_text: SpeechToText,
        harness: _Harness,
        resource_manager: ResourceManager,
        text_to_speech: TextToSpeech | None = None,
        tts_enabled: bool = True,
        tts_enabled_game_mode: bool = True,
        post_speech_cooldown_seconds: float = 0.75,
        sample_rate: int = 16000,
        output: Callable[[str], None] = print,
        cooldown_waiter: Callable[[float], None] = time.sleep,
    ) -> None:
        if post_speech_cooldown_seconds < 0:
            raise ValueError(
                "post_speech_cooldown_seconds não pode ser negativo."
            )
        self.listener = listener
        self.recorder = recorder
        self.speech_to_text = speech_to_text
        self.harness = harness
        self.resource_manager = resource_manager
        self.text_to_speech = text_to_speech
        self.tts_enabled = tts_enabled
        self.tts_enabled_game_mode = tts_enabled_game_mode
        self.post_speech_cooldown_seconds = (
            post_speech_cooldown_seconds
        )
        self.sample_rate = sample_rate
        self.output = output
        self.cooldown_waiter = cooldown_waiter
        self._closed = False

    def run(self) -> None:
        try:
            self.listener.start()
            self.output('Aguardando "Hey Jarvis"...')
            while True:
                if self.process_once():
                    self.output('Aguardando "Hey Jarvis"...')
        except KeyboardInterrupt:
            self.output("Modo voice encerrado.")
        finally:
            self.close()

    def process_once(self) -> bool:
        if self.listener.get_event() is None:
            return False

        self.resource_manager.activate()
        wake_state_reset = False
        try:
            self.output("Ouvindo...")
            audio = self.recorder.record()
            if not audio:
                return True

            try:
                result = self.speech_to_text.transcribe(
                    audio,
                    sample_rate=self.sample_rate,
                )
            except Exception as error:
                self.output(f"Erro ao transcrever áudio: {error}")
                return True

            text = result.text.strip()
            if not text:
                return True

            self.output(f"Você: {text}")
            response = self.harness.processar_mensagem(
                text,
                manage_runtime=False,
            )
            self.output(f"Jarvis: {response}")
            wake_state_reset = self._speak_response(response)
            return True
        finally:
            if not wake_state_reset:
                self.listener.reset()
            self.resource_manager.idle()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.listener.close()
        finally:
            try:
                self.speech_to_text.close()
            finally:
                if self.text_to_speech is not None:
                    self.text_to_speech.close()
                self._closed = True

    def _speak_response(self, response: str) -> bool:
        if not self._tts_is_allowed():
            return False

        spoken_text = prepare_text_for_speech(response)
        if not spoken_text:
            return False

        self.listener.stop()
        try:
            try:
                text_to_speech = self.text_to_speech
                if text_to_speech is not None:
                    text_to_speech.speak(spoken_text)
            except Exception as error:
                self.output(
                    "Aviso: não foi possível reproduzir a resposta por voz."
                )
                self.output(
                    f"Falha no TTS: {type(error).__name__}: {error}"
                )
        finally:
            self.cooldown_waiter(self.post_speech_cooldown_seconds)
            self.listener.reset()
            self.listener.start()
        return True

    def _tts_is_allowed(self) -> bool:
        if self.text_to_speech is None or not self.tts_enabled:
            return False
        return (
            self.tts_enabled_game_mode
            or not self.resource_manager.is_game_mode_active()
        )
