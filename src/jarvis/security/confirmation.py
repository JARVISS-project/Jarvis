"""Providers determinísticos de confirmação de segurança."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
import time

from jarvis.activation.listener import WakeWordListener
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.cancellation import (
    CancellationController,
    CancellationInterpreter,
)
from jarvis.security.risk import RiskLevel
from jarvis.security.confirmation_request import (
    ConfirmationPurpose,
    ConfirmationRequest,
)
from jarvis.security.confirmation_intent import (
    ConfirmationIntent,
    ConfirmationInterpreter,
    ConfirmationResult,
)
from jarvis.speech.base import SpeechToText
from jarvis.speech.validator import TranscriptionProfile, TranscriptionValidator
from jarvis.tts.base import TextToSpeech
from jarvis.voice.audio_session import VoiceAudioSession
from jarvis.voice.state import VoicePipelineState


class ConfirmationProvider(ABC):
    @abstractmethod
    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        raise NotImplementedError

    def confirm_request(self, request: ConfirmationRequest) -> bool:
        return self.confirm_result(request).accepts(request.purpose)

    def confirm_result(self, request: ConfirmationRequest) -> ConfirmationResult:
        confirmed = self.confirm(request.action, request.target, request.risk)
        if request.purpose is ConfirmationPurpose.TARGET:
            return ConfirmationResult(target_confirmed=confirmed)
        return ConfirmationResult(action_authorized=confirmed)

    def clear_pending_confirmation(self) -> None:
        """Descarta estado auxiliar sem produzir autorização."""


class KeyboardConfirmation(ConfirmationProvider):
    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        return self.confirm_request(
            ConfirmationRequest(
                action, target, risk, security_prompt(action, target)
            )
        )

    def confirm_request(self, request: ConfirmationRequest) -> bool:
        print("\n=== CONFIRMAÇÃO DE SEGURANÇA ===")
        print(f"Ação: {request.action}")
        print(f"Alvo: {request.target}")
        print(f"Risco: {request.risk.name}")
        return input("Autorizar? [s/N]: ").strip().lower() == "s"

    def confirm_result(self, request: ConfirmationRequest) -> ConfirmationResult:
        confirmed = self.confirm_request(request)
        if request.purpose is ConfirmationPurpose.TARGET:
            return ConfirmationResult(target_confirmed=confirmed)
        return ConfirmationResult(action_authorized=confirmed)


HumanConfirmation = KeyboardConfirmation


class VoiceConfirmation(ConfirmationProvider):
    def __init__(
        self,
        listener: WakeWordListener,
        recorder: UtteranceRecorder,
        speech_to_text: SpeechToText,
        text_to_speech: TextToSpeech | None,
        resource_manager: ResourceManager,
        sample_rate: int = 16000,
        keyboard_fallback: bool = False,
        fallback: ConfirmationProvider | None = None,
        validator: TranscriptionValidator | None = None,
        output: Callable[[str], None] = print,
        interpreter: ConfirmationInterpreter | None = None,
        cancellation: CancellationController | None = None,
        audio_session: VoiceAudioSession | None = None,
        retry_recorder: UtteranceRecorder | None = None,
        post_speech_cooldown_seconds: float = 0.0,
        cooldown_waiter: Callable[[float], None] = time.sleep,
        state_callback: Callable[[VoicePipelineState], None] | None = None,
        debug_enabled: bool = False,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.listener = listener
        self.recorder = recorder
        self.speech_to_text = speech_to_text
        self.text_to_speech = text_to_speech
        self.resource_manager = resource_manager
        self.sample_rate = sample_rate
        self.keyboard_fallback = keyboard_fallback
        self.fallback = fallback or KeyboardConfirmation()
        self.validator = validator or TranscriptionValidator()
        self.output = output
        self.interpreter = interpreter or ConfirmationInterpreter()
        self.cancellation = cancellation or CancellationController()
        self.cancellation_interpreter = CancellationInterpreter()
        self.audio_session = audio_session
        self.retry_recorder = retry_recorder or recorder
        self.post_speech_cooldown_seconds = post_speech_cooldown_seconds
        self.cooldown_waiter = cooldown_waiter
        self.state_callback = state_callback
        self.debug_enabled = debug_enabled
        self.monotonic_clock = monotonic_clock
        self._confirmed_target: str | None = None
        self._pending_request: ConfirmationRequest | None = None

    @property
    def pending_request(self) -> ConfirmationRequest | None:
        return self._pending_request

    def bind_state_callback(
        self,
        callback: Callable[[VoicePipelineState], None],
    ) -> None:
        self.state_callback = callback

    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        return self.confirm_request(
            ConfirmationRequest(
                action, target, risk, security_prompt(action, target)
            )
        )

    def confirm_result(self, request: ConfirmationRequest) -> ConfirmationResult:
        self.resource_manager.confirmation()
        self.listener.stop()
        self._pending_request = request
        confirmation_result: ConfirmationResult | None = None
        voice_failed = False
        final_timeout = False
        final_rejected = False
        try:
            if self.text_to_speech is None:
                raise RuntimeError("TTS indisponível para confirmação por voz.")
            prompt = request.prompt_text
            if (
                request.purpose is ConfirmationPurpose.SECURITY
                and self._confirmed_target is not None
                and Path(request.target).as_posix().casefold().endswith(
                    self._confirmed_target.casefold()
                )
            ):
                prompt = security_prompt(
                    request.action,
                    request.target,
                    prefix="Certo.",
                    risk=request.risk,
                )
            for attempt in range(2):
                recorder = (
                    self.recorder if attempt == 0 else self.retry_recorder
                )
                audio = self._speak_then_capture(prompt, recorder)
                if not audio:
                    voice_failed = True
                    final_timeout = True
                    final_rejected = False
                    if attempt == 0:
                        prompt = "Ainda deseja que eu faça isso?"
                        continue
                    break
                try:
                    transcription = self.speech_to_text.transcribe(
                        audio, self.sample_rate
                    )
                except Exception:
                    voice_failed = True
                    final_timeout = False
                    final_rejected = True
                    if attempt == 0:
                        prompt = "Não entendi sua confirmação. Pode repetir?"
                        continue
                    break
                if not self.validator.validate(
                    transcription,
                    captured_samples=len(audio),
                    sample_rate=self.sample_rate,
                    profile=TranscriptionProfile.CONFIRMATION,
                ).accepted:
                    voice_failed = True
                    final_timeout = False
                    final_rejected = True
                    if attempt == 0:
                        prompt = "Não entendi sua confirmação. Pode repetir?"
                        continue
                    break
                if self.cancellation_interpreter.is_cancellation(
                    transcription.text
                ):
                    self.cancellation.request_cancel()
                    confirmation_result = ConfirmationResult(denied=True)
                    break
                interpreted = self.interpreter.interpret_result(
                    transcription.text, request.purpose
                )
                self.output(f"Você [confirmação]: {transcription.text}")
                self.output(
                    "Confirmação: "
                    f"target={'CONFIRMED' if interpreted.target_confirmed else 'NO'} "
                    f"action={'AUTHORIZED' if interpreted.action_authorized else 'NO'}"
                )
                if interpreted.accepts(request.purpose):
                    confirmation_result = interpreted
                    if request.purpose is ConfirmationPurpose.TARGET:
                        self._confirmed_target = request.target
                    break
                if interpreted.denied:
                    confirmation_result = interpreted
                    break
                if attempt == 0:
                    prompt = (
                        "Não entendi uma confirmação clara. Deseja "
                        "autorizar? Diga sim ou não."
                    )
        except Exception:
            voice_failed = True
        if confirmation_result is None:
            self.output("Não consegui confirmar. A ação não foi realizada.")
            if self.keyboard_fallback and voice_failed:
                self.output("Confirmação por voz falhou. Usando teclado.")
            fallback_confirmed = (
                self.fallback.confirm_request(request)
                if self.keyboard_fallback and voice_failed
                else False
            )
            confirmation_result = (
                ConfirmationResult(
                    target_confirmed=fallback_confirmed,
                    timed_out=final_timeout and not fallback_confirmed,
                    transcription_rejected=(
                        final_rejected and not fallback_confirmed
                    ),
                )
                if request.purpose is ConfirmationPurpose.TARGET
                else ConfirmationResult(
                    action_authorized=fallback_confirmed,
                    timed_out=final_timeout and not fallback_confirmed,
                    transcription_rejected=(
                        final_rejected and not fallback_confirmed
                    ),
                )
            )
        self.listener.stop()

        decision = confirmation_result.accepts(request.purpose)
        if decision:
            message = "Confirmado."
        elif confirmation_result.timed_out or (
            confirmation_result.transcription_rejected
        ):
            message = "Não consegui confirmar. A ação não foi realizada."
        else:
            message = "Ação cancelada."
        try:
            if self.text_to_speech is not None:
                self.text_to_speech.speak(message)
        except Exception:
            self.output(message)
        finally:
            self.listener.reset()
            self.listener.start()
            self.resource_manager.activate()
            self._pending_request = None
            if request.purpose is ConfirmationPurpose.SECURITY:
                self._confirmed_target = None
        return confirmation_result

    def _speak_then_capture(
        self,
        prompt: str,
        recorder: UtteranceRecorder,
    ) -> list[int]:
        if self.audio_session is not None:
            self.audio_session.stop_stream()
        else:
            self.listener.stop()
        self._set_voice_state(VoicePipelineState.SPEAKING)
        # A confirmação precisa ser visível no terminal também. Isso deixa
        # explícito quando a janela de resposta começa e evita a sensação de
        # que o Jarvis pediu autorização "em silêncio".
        self.output(f"Jarvis: {prompt}")
        text_to_speech = self.text_to_speech
        if text_to_speech is None:
            raise RuntimeError("TTS indisponível para confirmação por voz.")
        speak_async = getattr(text_to_speech, "speak_async", None)
        if callable(speak_async):
            self._debug("confirmation tts started")
            playback = speak_async(prompt)
            playback.wait()
            self._debug("confirmation tts finished")
        else:
            self._debug("confirmation tts started")
            text_to_speech.speak(prompt)
            self._debug("confirmation tts finished")
        self.cooldown_waiter(self.post_speech_cooldown_seconds)
        self.resource_manager.confirmation()
        self._set_voice_state(VoicePipelineState.CONFIRMATION)
        timeout = getattr(recorder, "pre_speech_timeout_seconds", None)
        if timeout is None:
            timeout = getattr(recorder, "max_recording_seconds", None)
        suffix = f"={float(timeout):.1f}" if timeout is not None else ""
        self._debug(f"confirmation timeout{suffix}")
        if self.audio_session is not None:
            audio = self.audio_session.capture("confirmation", recorder)
            self.audio_session.stop_stream()
            return audio
        self.listener.start()
        try:
            return recorder.record()
        finally:
            self.listener.stop()

    def _set_voice_state(self, state: VoicePipelineState) -> None:
        if self.state_callback is not None:
            self.state_callback(state)

    def _debug(self, message: str) -> None:
        if self.debug_enabled:
            self.output(
                f"[voice] t={self.monotonic_clock():.3f} {message}"
            )

    def clear_pending_confirmation(self) -> None:
        self._confirmed_target = None
        self._pending_request = None

    @staticmethod
    def _prompt(action: str, target: str) -> str:
        safe_name = Path(target).name.replace(".", " ponto ").replace("_", " ")
        safe_name = " ".join(safe_name.split())[-160:]
        action_label = {
            "delete_file": "a exclusão de",
            "create_file": "a criação de",
            "edit_file": "a edição de",
            "move_file": "a movimentação de",
            "obsidian_create_note": "a criação da nota",
            "obsidian_append_note": "a adição de conteúdo à nota",
            "confirm_file_target": "que o arquivo pretendido é",
        }.get(action, "a ação sobre")
        return (
            f"Esta ação exige confirmação. Deseja autorizar "
            f"{action_label} {safe_name}?"
        )


def security_prompt(
    action: str,
    target: str,
    prefix: str = "Entendi.",
    risk: RiskLevel | None = None,
) -> str:
    name = Path(target).name.replace(".", " ponto ").replace("_", " ")
    verb = {
        "delete_file": "excluir",
        "create_file": "criar",
        "edit_file": "editar",
        "move_file": "mover",
        "obsidian_create_note": "criar a nota",
        "obsidian_append_note": "adicionar conteúdo à nota",
    }.get(action, "executar a ação sobre")
    warning = (
        " Esta é uma ação de alto risco."
        if risk is not None and risk >= RiskLevel.HIGH
        else ""
    )
    return f"{prefix} Você quer {verb} {name}.{warning} Posso fazer?"
