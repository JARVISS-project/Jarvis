"""Providers determinísticos de confirmação de segurança."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

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
        self._confirmed_target: str | None = None

    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        return self.confirm_request(
            ConfirmationRequest(
                action, target, risk, security_prompt(action, target)
            )
        )

    def confirm_result(self, request: ConfirmationRequest) -> ConfirmationResult:
        self.resource_manager.confirmation()
        self.listener.stop()
        confirmation_result: ConfirmationResult | None = None
        voice_failed = False
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
                if self.audio_session is not None:
                    audio = self.audio_session.speak_then_capture(
                        prompt,
                        self.text_to_speech,
                        self.recorder,
                    )
                    self.audio_session.stop_stream()
                else:
                    self.text_to_speech.speak(prompt)
                    self.listener.start()
                    audio = self.recorder.record()
                    self.listener.stop()
                if not audio:
                    voice_failed = True
                    break
                transcription = self.speech_to_text.transcribe(
                    audio, self.sample_rate
                )
                if not self.validator.validate(
                    transcription,
                    captured_samples=len(audio),
                    sample_rate=self.sample_rate,
                    profile=TranscriptionProfile.CONFIRMATION,
                ).accepted:
                    voice_failed = True
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
            self.output("Não consegui confirmar com segurança.")
            if self.keyboard_fallback and voice_failed:
                self.output("Confirmação por voz falhou. Usando teclado.")
            fallback_confirmed = (
                self.fallback.confirm_request(request)
                if self.keyboard_fallback and voice_failed
                else False
            )
            confirmation_result = (
                ConfirmationResult(target_confirmed=fallback_confirmed)
                if request.purpose is ConfirmationPurpose.TARGET
                else ConfirmationResult(
                    action_authorized=fallback_confirmed
                )
            )
        self.listener.stop()

        decision = confirmation_result.accepts(request.purpose)
        message = "Confirmado." if decision else "Ação cancelada."
        try:
            if self.text_to_speech is not None:
                self.text_to_speech.speak(message)
        except Exception:
            self.output(message)
        finally:
            self.listener.reset()
            self.listener.start()
            self.resource_manager.activate()
            if request.purpose is ConfirmationPurpose.SECURITY:
                self._confirmed_target = None
        return confirmation_result

    def clear_pending_confirmation(self) -> None:
        self._confirmed_target = None

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
