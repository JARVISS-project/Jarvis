"""Local administrative preferences and shared, bounded speech output."""
from __future__ import annotations

from copy import copy
from importlib.util import find_spec
from threading import Event, RLock, Thread

from jarvis.tts.base import SpeechPlaybackHandle, TextToSpeech
from jarvis.tts.factory import build_text_to_speech
from jarvis.tts.voices import installed_voices
from jarvis.tts.reference import import_reference, local_model_directory

PREVIEW_TEXT = "Esta é uma prévia da voz local. Você pode ajustar o ritmo nas preferências."
MAX_SPOKEN_CHARACTERS = 4000


class ConversationPreferences(TextToSpeech):
    """Same sink for UI and voice; never accepts LLM commands or model paths."""

    def __init__(self, config, backend=None, *, factory=build_text_to_speech,
                 audio_session=None, resume_input=lambda: True) -> None:
        self.config = config
        self._backend = backend
        self._factory = factory
        self._lock = RLock()
        self._signature = self._key() if backend is not None else None
        self._active = None
        self._closed = False
        self.audio_session = audio_session
        self.resume_input = resume_input
        self._ui_worker = None

    def _key(self):
        return (self.config.tts_provider, self.config.tts_piper_model_path,
                self.config.tts_piper_config_path, self.config.tts_piper_pace,
                self.config.tts_reference_audio_path, self.config.tts_device,
                self.config.tts_language_id, self.config.tts_chatterbox_exaggeration,
                self.config.tts_chatterbox_cfg_weight)

    def inventory(self) -> dict:
        voices = installed_voices(self.config)
        current = next((v.id for v in voices
                        if v.model == self.config.tts_piper_model_path.resolve()), "")
        dependency = find_spec("piper") is not None and find_spec("sounddevice") is not None
        neural = find_spec('chatterbox') is not None and find_spec('sounddevice') is not None
        reference = self.config.tts_reference_audio_path
        neural_ready = neural and local_model_directory() is not None
        with self._lock:
            error = self._active.error if self._active and self._active.finished else None
            error = error or getattr(self._backend, "last_primary_error", None)
            busy = bool(self._active and not self._active.finished)
        diagnostic = ("Falha na síntese ou saída de áudio; texto continua disponível."
                      if error else "Piper indisponível: instale o extra fast-voice."
                      if not dependency else "Nenhum par de voz Piper compatível instalado."
                      if not current else "Falando…" if busy else "Voz local disponível.")
        available = dependency and bool(current)
        if self.config.tts_provider == 'chatterbox':
            available = neural_ready and bool(reference and reference.is_file())
            diagnostic = ('Falha na voz neural; texto continua disponível. Nenhuma outra voz foi usada.' if error else
                          'Componente neural opcional ausente nesta instalação.' if not neural else
                          'Modelo neural ausente no computador. Nenhum download iniciado.' if not neural_ready else
                          'Importe uma amostra MP3 ou WAV para usar sua voz.' if not reference else
                          'Amostra indisponível; importe novamente.' if not reference.is_file() else
                          'Gerando ou reproduzindo voz…' if busy else 'Voz com sua amostra pronta para prévia local.')
        elif self.config.tts_provider == 'sapi':
            import sys
            available = sys.platform == 'win32'
            diagnostic = 'Falha na saída de áudio.' if error else 'Falando…' if busy else 'Voz do Windows selecionada.'
        return {"user_display_name": self.config.user_display_name,
                "tone": self.config.assistant_tone, "detail": self.config.assistant_detail,
                "opinions": self.config.assistant_opinions, "spoken": self.config.tts_enabled,
                "voice": current, "pace": self.config.tts_piper_pace,
                "voices": [{"id": v.id, "label": v.label} for v in voices],
                "provider": self.config.tts_provider,
                "reference": bool(reference and reference.is_file()),
                "neural_installed": neural, "neural_ready": neural_ready,
                "speed_supported": self.config.tts_provider in {"auto", "piper"} and bool(current),
                "diagnostic": diagnostic, "busy": busy,
                "available": available}

    def configure_voice(self, *, provider: str) -> dict:
        if provider not in {'auto', 'piper', 'sapi', 'chatterbox'}:
            raise ValueError('Selecione um mecanismo de voz disponível.')
        with self._lock:
            self._check_idle()
            self.config.save_voice_settings(provider=provider)
        return self.inventory()

    def import_voice(self, *, source: str) -> dict:
        with self._lock:
            self._check_idle()
            destination = import_reference(source, self.config.settings_path.parent / 'voices' / 'references')
            self.config.save_voice_settings(provider='chatterbox', reference_audio=destination)
        return self.inventory()

    def _check_idle(self):
        if self._closed or (self._active and not self._active.finished):
            raise PermissionError('Interrompa a fala antes de alterar a voz.')

    def save(self, **arguments) -> dict:
        with self._lock:
            if self._closed or (self._active and not self._active.finished):
                raise PermissionError("Interrompa a fala antes de alterar preferências.")
            self.config.save_conversation_preferences(**arguments)
        return self.inventory()

    def preview(self) -> dict:
        if not self.inventory()["available"]:
            raise PermissionError("Voz local indisponível.")
        self.speak_ui_async(PREVIEW_TEXT, preview=True)
        return self.inventory()

    def speak(self, text: str) -> None:
        handle = self.speak_async(text)
        if not handle.wait(120):
            handle.stop()
            raise TimeoutError("Fala interrompida pelo limite de duração.")

    def speak_async(self, text: str) -> SpeechPlaybackHandle:
        return self._speak_async(text)

    def speak_ui_async(self, text: str, *, preview: bool = False) -> SpeechPlaybackHandle:
        if self.audio_session is None:
            return self._speak_async(text, preview=preview)
        # Voice turns already own audio in their worker. UI output acquires the
        # SAME audio session on one bounded worker, never over a live capture.
        finished, interrupted = Event(), Event()
        errors = []
        def stop():
            interrupted.set()
            if self._backend is not None:
                self._backend.stop()
        handle = SpeechPlaybackHandle(finished, stop, lambda: errors[0] if errors else None,
                                      interrupted_getter=interrupted.is_set)
        def output():
            playback = None
            try:
                with self.audio_session.ownership("playback"):
                    if interrupted.is_set() or self._closed:
                        return
                    self.audio_session.stop_stream()
                    try:
                        playback = self._speak_async(text, preview=preview, reservation=handle)
                        if interrupted.is_set():
                            playback.stop()
                        if not playback.wait(120):
                            playback.stop()
                            raise TimeoutError("Limite de fala atingido.")
                    finally:
                        if (playback is None or playback.finished) and not self._closed and self.resume_input():
                            self.audio_session.start_stream()
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()
        with self._lock:
            if self._closed or (self._active and not self._active.finished):
                raise RuntimeError("Saída de voz ocupada ou encerrada.")
            self._active = handle
            self._ui_worker = Thread(target=output, name="jarvis-ui-speech", daemon=False)
            self._ui_worker.start()
        return handle

    def _speak_async(self, text: str, *, preview: bool = False, reservation=None) -> SpeechPlaybackHandle:
        with self._lock:
            if self._closed:
                raise RuntimeError("Saída de voz encerrada.")
            if not text.strip() or (not preview and not self.config.tts_enabled):
                done = Event()
                done.set()
                return SpeechPlaybackHandle(done, lambda: None)
            if len(text) > MAX_SPOKEN_CHARACTERS:
                raise ValueError("Resposta excede o limite de fala; consulte o texto.")
            if self.config.tts_provider in {"auto", "piper"} and not self.inventory()["voice"]:
                raise RuntimeError("Voz configurada indisponível; nenhuma troca automática.")
            if self.config.tts_provider == 'chatterbox' and not self.inventory()['available']:
                raise RuntimeError('Voz neural indisponível; configure a amostra e o modelo local. Nenhuma troca automática.')
            if self._active and not self._active.finished and self._active is not reservation:
                raise RuntimeError("Saída de voz ocupada; nenhuma fala enfileirada.")
            if self._backend is None or self._signature != self._key():
                if self._backend is not None:
                    self._backend.close()
                effective = copy(self.config)
                effective.tts_enabled = True
                self._backend = self._factory(effective)
                self._signature = self._key()
            if self._backend is None:
                raise RuntimeError("Saída de voz indisponível.")
            handle = self._backend.speak_async(text)
            if reservation is None:
                self._active = handle
            return handle

    def stop(self) -> dict:
        with self._lock:
            if self._active and not self._active.finished:
                self._active.stop()
            elif self._backend is not None:
                self._backend.stop()
        return self.inventory()

    @property
    def fast_acknowledgement(self):
        return getattr(self._backend, "fast_acknowledgement", True)

    @property
    def last_synthesis_seconds(self):
        return getattr(self._backend, "last_synthesis_seconds", None)

    def warm_up(self) -> None:
        if self._backend is not None:
            method = getattr(self._backend, "warm_up", None)
            if callable(method):
                method()

    def close(self) -> None:
        if self._closed:
            return
        self.stop()
        self._closed = True
        if self._ui_worker is not None:
            self._ui_worker.join()
        with self._lock:
            if self._backend is not None:
                self._backend.close()
