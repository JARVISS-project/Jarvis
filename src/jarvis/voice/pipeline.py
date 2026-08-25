from collections.abc import Callable
import time
from typing import Protocol

from jarvis.activation.listener import WakeWordListener
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.cancellation import (
    CancellationController,
    CancellationInterpreter,
)
from jarvis.speech.base import SpeechToText
from jarvis.speech.normalizer import TranscriptNormalizer
from jarvis.speech.validator import (
    TranscriptionProfile,
    TranscriptionQuality,
    TranscriptionValidation,
    TranscriptionValidator,
)
from jarvis.tts.base import TextToSpeech
from jarvis.tts.text import prepare_text_for_speech
from jarvis.voice.barge_in import BargeInController
from jarvis.voice.audio_session import VoiceAudioSession
from jarvis.voice.capture_result import VoiceCaptureResult, VoiceCaptureStatus
from jarvis.voice.conversation_end import ConversationEndInterpreter
from jarvis.voice.state import VoicePipelineState
from jarvis.voice.turn_end import IncompleteUtteranceDetector


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
        transcription_validator: TranscriptionValidator | None = None,
        transcript_normalizer: TranscriptNormalizer | None = None,
        conversation_followup_enabled: bool = False,
        followup_recorder: UtteranceRecorder | None = None,
        game_mode_followup_recorder: UtteranceRecorder | None = None,
        question_followup_recorder: UtteranceRecorder | None = None,
        repair_recorder: UtteranceRecorder | None = None,
        incomplete_turn_recorder: UtteranceRecorder | None = None,
        cancellation: CancellationController | None = None,
        barge_in_controller: BargeInController | None = None,
        audio_session: VoiceAudioSession | None = None,
        acknowledgement_enabled: bool = False,
        debug_enabled: bool = False,
        cancel_max_no_speech_probability: float = 0.35,
        cancel_min_average_log_probability: float = -0.6,
        cancel_min_speech_duration_seconds: float = 0.2,
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
        self.transcription_validator = transcription_validator
        self.transcript_normalizer = transcript_normalizer or TranscriptNormalizer()
        self.conversation_followup_enabled = conversation_followup_enabled
        self.followup_recorder = followup_recorder
        self.game_mode_followup_recorder = game_mode_followup_recorder
        self.question_followup_recorder = question_followup_recorder
        self.repair_recorder = repair_recorder
        self.incomplete_turn_recorder = incomplete_turn_recorder
        self.incomplete_utterance_detector = IncompleteUtteranceDetector()
        self.cancellation = cancellation or CancellationController()
        self.cancellation_interpreter = CancellationInterpreter()
        self.barge_in_controller = barge_in_controller
        self._pending_barge_in_text: str | None = None
        self.audio_session = audio_session
        self.acknowledgement_enabled = acknowledgement_enabled
        self.debug_enabled = debug_enabled
        self.cancel_max_no_speech_probability = (
            cancel_max_no_speech_probability
        )
        self.cancel_min_average_log_probability = (
            cancel_min_average_log_probability
        )
        self.cancel_min_speech_duration_seconds = (
            cancel_min_speech_duration_seconds
        )
        self.conversation_end_interpreter = ConversationEndInterpreter()
        self.state = VoicePipelineState.WAITING_WAKE
        self._last_response = ""
        self._end_conversation = False
        self._closed = False

    def run(self) -> None:
        try:
            self.listener.start()
            self._enter_waiting_wake(announce=True)
            while True:
                if self.process_once():
                    self._enter_waiting_wake(announce=True)
        except KeyboardInterrupt:
            self.output("Modo voice encerrado.")
        finally:
            self.close()

    def process_once(self) -> bool:
        if self.listener.get_event() is None:
            return False
        detector = getattr(self.listener, "detector", None)
        score = getattr(detector, "last_score", None)
        frames = getattr(detector, "last_confirmation_frames", None)
        details = []
        if score is not None:
            details.append(f"score={score:.3f}")
        if frames is not None:
            details.append(f"confirmed_frames={frames}")
        suffix = f" {' '.join(details)}" if details else ""
        self._debug(f"wake detected{suffix}")

        self.cancellation.clear()
        self._end_conversation = False
        if self.conversation_followup_enabled:
            self.resource_manager.conversation()
        else:
            self.resource_manager.activate()
        wake_state_reset = False
        try:
            capture = self._process_recording(self.recorder)
            wake_state_reset = capture.spoke
            if capture.repairable:
                capture = self._attempt_repair(
                    TranscriptionProfile.GENERAL_COMMAND
                )
                wake_state_reset = wake_state_reset or capture.spoke
            if not capture.succeeded:
                return True
            wake_state_reset = self._process_pending_barge_in(
                wake_state_reset
            )
            while self.conversation_followup_enabled:
                if self.cancellation.is_cancelled:
                    break
                followup = self._followup_recorder(
                    question=self._response_expects_answer()
                )
                if followup is None:
                    break
                self.resource_manager.conversation()
                self.state = VoicePipelineState.WAITING_FOLLOWUP
                self.output("Ouvindo continuação...")
                capture = self._process_recording(
                    followup,
                    announce=False,
                    empty_status=VoiceCaptureStatus.TIMEOUT,
                )
                wake_state_reset = wake_state_reset or capture.spoke
                if capture.repairable:
                    capture = self._attempt_repair(
                        TranscriptionProfile.FOLLOWUP
                    )
                    wake_state_reset = wake_state_reset or capture.spoke
                if not capture.succeeded or self._end_conversation:
                    break
                wake_state_reset = self._process_pending_barge_in(
                    wake_state_reset
                )
            return True
        finally:
            if not wake_state_reset:
                self.listener.reset()
            self.resource_manager.idle()
            self.cancellation.clear()
            self._set_state(VoicePipelineState.WAITING_WAKE)

    def _process_recording(
        self,
        recorder: UtteranceRecorder,
        announce: bool = True,
        empty_status: VoiceCaptureStatus = VoiceCaptureStatus.NO_SPEECH,
        profile: TranscriptionProfile | None = None,
    ) -> VoiceCaptureResult:
        if announce:
            self._set_state(VoicePipelineState.LISTENING)
            self.output("Ouvindo...")
        audio = (
            self.audio_session.capture("utterance", recorder)
            if self.audio_session is not None
            else recorder.record()
        )
        if not audio:
            self._debug(f"recorder result={empty_status.name}")
            return VoiceCaptureResult(empty_status)
        self._set_state(VoicePipelineState.LISTENING)
        profile = profile or (
            TranscriptionProfile.GENERAL_COMMAND if announce
            else TranscriptionProfile.FOLLOWUP
        )
        self._debug(f"speech gate=STARTED samples={len(audio)}")
        try:
            result, validation = self._transcribe_and_validate(
                audio, profile
            )
        except Exception as error:
            self.output(f"Erro ao transcrever áudio: {error}")
            self._debug("stt result=INTERRUPTED")
            return VoiceCaptureResult(VoiceCaptureStatus.INTERRUPTED)
        # Preserve the spoken form for security-sensitive target resolution.
        # FileReferenceResolver applies path normalization with risk context.
        text = result.text.strip()
        if not validation.accepted:
            self._debug_transcription_rejection(
                result, profile, validation.reason
            )
            return VoiceCaptureResult(
                VoiceCaptureStatus.TRANSCRIPTION_REJECTED,
                repairable=(
                    validation.quality is TranscriptionQuality.BORDERLINE
                ),
                reason=validation.reason,
            )
        if self.incomplete_utterance_detector.is_possibly_incomplete(text):
            extended = self._capture_incomplete_turn_extension()
            if extended:
                audio = [*audio, *extended]
                try:
                    result, validation = self._transcribe_and_validate(
                        audio, profile
                    )
                except Exception as error:
                    self.output(f"Erro ao transcrever áudio: {error}")
                    return VoiceCaptureResult(
                        VoiceCaptureStatus.INTERRUPTED
                    )
                text = result.text.strip()
                if not validation.accepted:
                    self._debug_transcription_rejection(
                        result, profile, validation.reason
                    )
                    return VoiceCaptureResult(
                        VoiceCaptureStatus.TRANSCRIPTION_REJECTED,
                        repairable=(
                            validation.quality
                            is TranscriptionQuality.BORDERLINE
                        ),
                        reason=validation.reason,
                    )
        self._debug_transcription_accepted(result, profile)
        cancel_candidate = self.cancellation_interpreter.is_cancellation(text)
        if (
            cancel_candidate
            and not self._is_reliable_control_transcription(result)
        ):
            self._debug("cancel intent=NO reason=low_quality")
            return VoiceCaptureResult(
                VoiceCaptureStatus.TRANSCRIPTION_REJECTED
            )
        cancelled = cancel_candidate
        self._debug(f"cancel intent={'YES' if cancelled else 'NO'}")
        if cancelled:
            self.cancellation.request_cancel()
            self.output(f"Você [cancelamento]: {text}")
            self.output("Ação cancelada.")
            return VoiceCaptureResult(VoiceCaptureStatus.USER_CANCELLED)
        if self.conversation_end_interpreter.should_end(text):
            self.output(f"Você: {text}")
            self._end_conversation = True
            spoke = self._speak_response("Até mais.")
            return VoiceCaptureResult(
                VoiceCaptureStatus.INTERRUPTED, spoke=spoke
            )
        processed, spoke = self._process_text(text)
        return VoiceCaptureResult(
            VoiceCaptureStatus.SUCCESS if processed
            else VoiceCaptureStatus.INTERRUPTED,
            spoke=spoke,
        )

    def _capture_incomplete_turn_extension(self) -> list[int]:
        recorder = self.incomplete_turn_recorder
        if recorder is None:
            return []
        self._debug("turn end=possibly_incomplete extension=1")
        return (
            self.audio_session.capture("incomplete_turn", recorder)
            if self.audio_session is not None
            else recorder.record()
        )

    def _process_text(self, text: str) -> tuple[bool, bool]:
        self._set_state(VoicePipelineState.PROCESSING)
        self.output(f"Você: {text}")
        self._acknowledge(text)
        response = self.harness.processar_mensagem(
            text,
            manage_runtime=False,
        )
        self._last_response = response
        self.output(f"Jarvis: {response}")
        return True, self._speak_response(response)

    def _acknowledge(self, text: str) -> None:
        if (
            not self.acknowledgement_enabled
            or not self._tts_is_allowed()
            or self._looks_security_sensitive(text)
        ):
            return
        text_to_speech = self.text_to_speech
        if text_to_speech is None:
            return
        if self.audio_session is not None:
            self.audio_session.stop_stream()
        else:
            self.listener.stop()
        try:
            text_to_speech.speak("Entendi.")
        finally:
            if self.audio_session is not None:
                self.audio_session.start_stream()
            else:
                self.listener.start()

    @staticmethod
    def _looks_security_sensitive(text: str) -> bool:
        normalized = text.casefold()
        return any(
            word in normalized
            for word in (
                "apague", "apagar", "delete", "exclua", "excluir",
                "edite", "editar", "crie", "criar", "mova", "mover",
            )
        )

    def _process_pending_barge_in(self, wake_state_reset: bool) -> bool:
        while (
            self._pending_barge_in_text is not None
            and not self.cancellation.is_cancelled
        ):
            text = self._pending_barge_in_text
            self._pending_barge_in_text = None
            _, spoke = self._process_text(text)
            wake_state_reset = wake_state_reset or spoke
        return wake_state_reset

    def _followup_recorder(
        self,
        *,
        question: bool = False,
    ) -> UtteranceRecorder | None:
        if self.resource_manager.is_game_mode_active():
            return self.game_mode_followup_recorder or self.followup_recorder
        if question and self.question_followup_recorder is not None:
            return self.question_followup_recorder
        return self.followup_recorder

    def _response_expects_answer(self) -> bool:
        pending = getattr(self.harness, "has_pending_action", None)
        if callable(pending) and pending():
            return True
        return "?" in self._last_response

    def _enter_waiting_wake(self, *, announce: bool) -> None:
        if self.state is VoicePipelineState.WAITING_WAKE and not announce:
            return
        self._set_state(VoicePipelineState.WAITING_WAKE)
        if announce:
            self.output('Aguardando "Hey Jarvis"...')

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

        self._set_state(VoicePipelineState.SPEAKING)
        if self.audio_session is not None:
            self.audio_session.stop_stream()
        else:
            self.listener.stop()
        try:
            try:
                text_to_speech = self.text_to_speech
                if text_to_speech is not None:
                    if self.barge_in_controller is None:
                        text_to_speech.speak(spoken_text)
                    else:
                        playback = text_to_speech.speak_async(spoken_text)
                        if self.audio_session is not None:
                            self.audio_session.start_stream()
                        else:
                            self.listener.start()
                        audio = self.barge_in_controller.listen(playback)
                        playback.wait()
                        if audio:
                            self._handle_barge_in_audio(audio)
            except Exception as error:
                self.output(
                    "Aviso: não foi possível reproduzir a resposta por voz."
                )
                self.output(
                    f"Falha no TTS: {type(error).__name__}: {error}"
                )
        finally:
            # A janela de follow-up só poderá ser iniciada pelo chamador
            # depois que playback e cooldown terminarem neste bloco.
            self.cooldown_waiter(self.post_speech_cooldown_seconds)
            self.listener.reset()
            if self.audio_session is not None:
                self.audio_session.start_stream()
            else:
                self.listener.start()
        return True

    def _handle_barge_in_audio(self, audio: list[int]) -> None:
        try:
            result = self.speech_to_text.transcribe(
                audio,
                sample_rate=self.sample_rate,
            )
        except Exception as error:
            self.output(f"Erro ao transcrever interrupção: {error}")
            return
        text = result.text.strip()
        valid = bool(text) and (
            self.transcription_validator is None
            or self.transcription_validator.is_valid(
                result,
                captured_samples=len(audio),
                sample_rate=self.sample_rate,
            )
        )
        if not valid:
            self._debug_transcription_rejection(
                result,
                TranscriptionProfile.FOLLOWUP,
                "validation",
            )
            return
        cancel_candidate = self.cancellation_interpreter.is_cancellation(text)
        if (
            cancel_candidate
            and not self._is_reliable_control_transcription(result)
        ):
            self._debug("cancel intent=NO reason=low_quality")
            return
        cancelled = cancel_candidate
        self._debug(f"cancel intent={'YES' if cancelled else 'NO'}")
        if cancelled:
            self.cancellation.request_cancel()
            self.output(f"Você [cancelamento]: {text}")
            self.output("Ação cancelada.")
            text_to_speech = self.text_to_speech
            if text_to_speech is not None:
                if self.audio_session is not None:
                    self.audio_session.stop_stream()
                else:
                    self.listener.stop()
                try:
                    text_to_speech.speak("Cancelado.")
                except Exception:
                    pass
            return
        self._pending_barge_in_text = text

    def _is_reliable_control_transcription(self, result: object) -> bool:
        no_speech = getattr(result, "no_speech_probability", None)
        if (
            no_speech is not None
            and no_speech > self.cancel_max_no_speech_probability
        ):
            return False
        average_log_probability = getattr(
            result, "average_log_probability", None
        )
        if (
            average_log_probability is not None
            and average_log_probability
            < self.cancel_min_average_log_probability
        ):
            return False
        speech_duration = getattr(result, "duration_after_vad_seconds", None)
        return not (
            speech_duration is not None
            and speech_duration < self.cancel_min_speech_duration_seconds
        )

    def _transcribe_and_validate(
        self,
        audio: list[int],
        profile: TranscriptionProfile,
    ) -> tuple[object, TranscriptionValidation]:
        result = self.speech_to_text.transcribe(
            audio, sample_rate=self.sample_rate
        )
        validation = self._validate_transcription(result, audio, profile)
        if (
            validation.quality is TranscriptionQuality.BORDERLINE
        ):
            self._debug("quality result=BORDERLINE")
            self._debug("stt consensus retry=1 same_audio=true")
            first = result
            second = self.speech_to_text.transcribe_retry(
                audio, sample_rate=self.sample_rate
            )
            second_validation = self._validate_transcription(
                second, audio, profile
            )
            validator = self.transcription_validator
            if validator is not None:
                result, validation = validator.consensus(
                    first, second, second_validation
                )
                if validation.stability is not None:
                    self._debug(
                        "transcript stability="
                        f"{validation.stability:.3f}"
                    )
        return result, validation

    def _attempt_repair(
        self,
        profile: TranscriptionProfile,
    ) -> VoiceCaptureResult:
        prompt = "Não entendi direito. Repete pra mim."
        self.output(f"Jarvis: {prompt}")
        spoke = self._speak_response(prompt)
        self._set_state(VoicePipelineState.WAITING_REPAIR)
        self.output("Ouvindo...")
        recorder = self.repair_recorder or self.followup_recorder
        if recorder is None:
            return VoiceCaptureResult(
                VoiceCaptureStatus.INTERRUPTED, spoke=spoke
            )
        repaired = self._process_recording(
            recorder,
            announce=False,
            empty_status=VoiceCaptureStatus.TIMEOUT,
            profile=profile,
        )
        if repaired.succeeded:
            return VoiceCaptureResult(
                VoiceCaptureStatus.SUCCESS,
                spoke=spoke or repaired.spoke,
            )
        failure = "Não consegui entender."
        self.output(f"Jarvis: {failure}")
        failure_spoke = self._speak_response(failure)
        return VoiceCaptureResult(
            repaired.status,
            spoke=spoke or failure_spoke or repaired.spoke,
            reason=repaired.reason,
        )

    def _validate_transcription(
        self,
        result: object,
        audio: list[int],
        profile: TranscriptionProfile,
    ) -> TranscriptionValidation:
        validator = self.transcription_validator
        if validator is None:
            accepted = bool(getattr(result, "text", "").strip())
            return TranscriptionValidation(
                (
                    TranscriptionQuality.ACCEPTED
                    if accepted
                    else TranscriptionQuality.REJECTED
                ),
                "accepted" if accepted else "empty_text",
            )
        return validator.validate(
            result,  # type: ignore[arg-type]
            captured_samples=len(audio),
            sample_rate=self.sample_rate,
            profile=profile,
        )

    def _debug_transcription_rejection(
        self,
        result: object,
        profile: TranscriptionProfile | None = None,
        reason: str = "rejected",
    ) -> None:
        metadata = []
        for label, attribute in (
            ("duration", "duration_seconds"),
            ("speech_duration", "duration_after_vad_seconds"),
            ("avg_logprob", "average_log_probability"),
            ("no_speech", "no_speech_probability"),
        ):
            value = getattr(result, attribute, None)
            if value is not None:
                metadata.append(f"{label}={value:.3f}")
        suffix = f" {' '.join(metadata)}" if metadata else ""
        quality = "BORDERLINE" if reason == "unstable_consensus" else "REJECTED"
        profile_text = (
            f" quality profile={profile.name}" if profile is not None else ""
        )
        self._debug(
            f"quality result={quality} reason={reason}{profile_text}{suffix}"
        )

    def _debug_transcription_accepted(
        self,
        result: object,
        profile: TranscriptionProfile,
    ) -> None:
        metadata = []
        for label, attribute in (
            ("speech_duration", "duration_after_vad_seconds"),
            ("avg_logprob", "average_log_probability"),
            ("no_speech", "no_speech_probability"),
        ):
            value = getattr(result, attribute, None)
            if value is not None:
                metadata.append(f"{label}={value:.3f}")
        suffix = f" {' '.join(metadata)}" if metadata else ""
        self._debug(
            f"quality profile={profile.name} result=ACCEPTED{suffix}"
        )

    def _debug(self, message: str) -> None:
        if self.debug_enabled:
            self.output(f"[voice] {message}")

    def _set_state(self, state: VoicePipelineState) -> None:
        previous = self.state
        self.state = state
        if previous is not state:
            self._debug(f"transition={previous.name} -> {state.name}")

    def _tts_is_allowed(self) -> bool:
        if self.text_to_speech is None or not self.tts_enabled:
            return False
        return (
            self.tts_enabled_game_mode
            or not self.resource_manager.is_game_mode_active()
        )
