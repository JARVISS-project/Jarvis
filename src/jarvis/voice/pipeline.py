from collections.abc import Callable
from contextlib import nullcontext
from threading import Event
import time
from typing import Protocol

from jarvis.activation.listener import WakeWordListener
from jarvis.application.conversation_log import ConversationLogBus
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.cancellation import (
    CancellationController,
    CancellationInterpreter,
)
from jarvis.speech.base import SpeechToText
from jarvis.speech.metrics import SpeechRecognitionMetricsRecorder
from jarvis.speech.ptbr_fallback import PtBrFallback
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
from jarvis.voice.capture_result import (
    NoSpeechReason,
    VoiceCaptureResult,
    VoiceCaptureStatus,
)
from jarvis.voice.phrases import ResponseVariationManager
from jarvis.voice.conversation_end import ConversationEndInterpreter
from jarvis.voice.state import VoicePipelineState
from jarvis.voice.ptt import PttController
from jarvis.voice.turn_end import IncompleteUtteranceDetector
from jarvis.voice.transcription_coordinator import TranscriptionCoordinator
from jarvis.voice.diagnostic import VoiceDiagnosticRecorder
from jarvis.voice.semantic_shadow import SemanticShadowObserver
from jarvis.voice.stt_shadow import SttCandidateShadow


class _Harness(Protocol):
    def processar_mensagem(
        self,
        mensagem: str,
        manage_runtime: bool = True,
        response_mode: str = "text",
        confirmation_session_id: str | None = None,
        confirmation_challenge_id: str | None = None,
    ) -> str: ...

    def warm_up_llm(self) -> None: ...

    def has_pending_confirmation(self) -> bool: ...

    def current_confirmation_context(self) -> tuple[str, str] | None: ...

    def pop_confirmation_event(self) -> tuple[str, int, str] | None: ...

    def cancel_pending_confirmation(self) -> bool: ...

    def close_confirmation_sessions(self) -> None: ...


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
        monotonic_clock: Callable[[], float] = time.monotonic,
        transcription_validator: TranscriptionValidator | None = None,
        transcription_metrics_recorder: SpeechRecognitionMetricsRecorder | None = None,
        transcription_fallback: PtBrFallback | None = None,
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
        wake_acknowledgement_enabled: bool = False,
        wake_word_label: str = "Hey Jarvis",
        phrase_manager: ResponseVariationManager | None = None,
        state_callback: Callable[[VoicePipelineState], None] | None = None,
        debug_enabled: bool = False,
        latency_debug_enabled: bool = False,
        warm_up_stt_on_start: bool = False,
        stt_preload_timeout_seconds: float = 30.0,
        warm_up_llm_on_start: bool = False,
        cancel_max_no_speech_probability: float = 0.35,
        cancel_min_average_log_probability: float = -0.6,
        cancel_min_speech_duration_seconds: float = 0.2,
        followup_timeout_seconds: float = 8.0,
        question_followup_timeout_seconds: float = 15.0,
        game_mode_followup_timeout_seconds: float = 4.0,
        conversation_log_bus: ConversationLogBus | None = None,
        diagnostic: VoiceDiagnosticRecorder | None = None,
        semantic_shadow_enabled: bool = False,
        semantic_shadow_factory: Callable[[], SemanticShadowObserver] | None = None,
        stt_candidate_shadow: SttCandidateShadow | None = None,
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
        self.monotonic_clock = monotonic_clock
        self.transcription_validator = transcription_validator
        self.transcription_metrics_recorder = transcription_metrics_recorder
        self.transcription_fallback = transcription_fallback
        self.diagnostic = diagnostic
        self.debug_enabled = debug_enabled
        self.latency_debug_enabled = latency_debug_enabled
        self.semantic_shadow: SemanticShadowObserver | None = None
        self.stt_candidate_shadow = stt_candidate_shadow
        if semantic_shadow_enabled:
            factory = semantic_shadow_factory or SemanticShadowObserver
            try:
                self.semantic_shadow = factory()
            except Exception as error:
                self._debug(
                    "semantic shadow initialization failed "
                    f"error={type(error).__name__}"
                )
        self._activation_origin = "unknown"
        if diagnostic is not None:
            set_diagnostic = getattr(harness, "set_voice_diagnostic", None)
            if callable(set_diagnostic):
                set_diagnostic(diagnostic)
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
        self.wake_acknowledgement_enabled = wake_acknowledgement_enabled
        self.wake_word_label = wake_word_label.strip() or "Hey Jarvis"
        self.phrase_manager = phrase_manager or ResponseVariationManager()
        self.state_callback = state_callback
        self.warm_up_stt_on_start = warm_up_stt_on_start
        self.stt_preload_timeout_seconds = stt_preload_timeout_seconds
        self.warm_up_llm_on_start = warm_up_llm_on_start
        self.cancel_max_no_speech_probability = (
            cancel_max_no_speech_probability
        )
        self.cancel_min_average_log_probability = (
            cancel_min_average_log_probability
        )
        self.cancel_min_speech_duration_seconds = (
            cancel_min_speech_duration_seconds
        )
        self.transcription_coordinator = TranscriptionCoordinator(
            self.speech_to_text,
            sample_rate=self.sample_rate,
            validator=self.transcription_validator,
            monotonic_clock=self.monotonic_clock,
            debug=self._debug,
            cancel_max_no_speech_probability=self.cancel_max_no_speech_probability,
            cancel_min_average_log_probability=self.cancel_min_average_log_probability,
            cancel_min_speech_duration_seconds=self.cancel_min_speech_duration_seconds,
            metrics_recorder=self.transcription_metrics_recorder,
            fallback=self.transcription_fallback,
            fallback_allowed=lambda: not self.resource_manager.is_game_mode_active(),
            trace=self._trace,
        )
        self.followup_timeout_seconds = followup_timeout_seconds
        self.question_followup_timeout_seconds = (
            question_followup_timeout_seconds
        )
        self.game_mode_followup_timeout_seconds = (
            game_mode_followup_timeout_seconds
        )
        self.conversation_log_bus = conversation_log_bus
        self.conversation_end_interpreter = ConversationEndInterpreter()
        self.state = VoicePipelineState.WAITING_WAKE
        self._last_response = ""
        self._end_conversation = False
        self._closed = False
        self._last_wake_miss_time = 0.0
        self._stop_requested = Event()
        self.ptt = audio_session.ptt if audio_session is not None else PttController()
        self._current_turn_started_at: float | None = None
        self._current_stt_seconds = 0.0
        self._last_tts_total_seconds = 0.0
        self._last_harness_seconds = 0.0

    def _trace(self, event: str, fields: dict[str, object]) -> None:
        from jarvis.application.delegation import looks_delegation
        if isinstance(fields.get('text'), str) and looks_delegation(fields['text']):
            fields = {**fields, 'text': '<private-delegation>'}
        from jarvis.integrations.google.drive_docs.routing import looks_private_document
        if isinstance(fields.get('text'), str) and looks_private_document(fields['text']):
            fields = {**fields, 'text': '<private-google>'}
        if self.diagnostic is not None:
            self.diagnostic.record(event, **fields)

    def run(self) -> None:
        try:
            if not self._warm_up_stt():
                self.output(
                    "Reconhecimento de voz indisponível; "
                    "a entrada por texto permanece disponível."
                )
                return
            self._warm_up_transcription_fallback()
            self._warm_up_llm()
            self.listener.start()
            self._enter_waiting_wake(announce=True)
            while not self._stop_requested.is_set() and not self._closed:
                if self.process_once():
                    self._enter_waiting_wake(announce=True)
        except KeyboardInterrupt:
            self.output("Modo voice encerrado.")
        except Exception:
            if not self._stop_requested.is_set():
                raise
        finally:
            self.close()

    def _warm_up_stt(self) -> bool:
        if not self.warm_up_stt_on_start:
            return True
        preload = getattr(self.speech_to_text, "preload", None)
        warm_up = getattr(self.speech_to_text, "warm_up", None)
        loader = preload if callable(preload) else warm_up
        if not callable(loader):
            return True
        self.output("Preparando reconhecimento de voz...")
        started = self.monotonic_clock()
        try:
            ready = (
                loader(self.stt_preload_timeout_seconds)
                if callable(preload)
                else (loader() is None)
            )
        except Exception as error:
            self._debug(f"stt warmup failed error={type(error).__name__}")
            return False
        if not ready:
            error_code = getattr(self.speech_to_text, "load_error", "unknown")
            self.output(f"Falha ao carregar STT (motivo: {error_code}).")
            return False
        elapsed = self.monotonic_clock() - started
        self._debug(f"stt warmup completed seconds={elapsed:.2f}")
        self.output("Reconhecimento de voz pronto.")
        return True

    def _warm_up_transcription_fallback(self) -> None:
        fallback = self.transcription_fallback
        if fallback is None or self.resource_manager.is_game_mode_active():
            return
        warm_up_async = getattr(fallback, "warm_up_async", None)
        if not callable(warm_up_async):
            return
        warm_up_async()
        self._debug("stt fallback warmup started")

    def _warm_up_llm(self) -> None:
        if not self.warm_up_llm_on_start:
            return
        warm_up = getattr(self.harness, "warm_up_llm", None)
        if not callable(warm_up):
            return
        self.output("Preparando assistente...")
        started = self.monotonic_clock()
        try:
            warm_up()
        except Exception as error:
            # Voice remains usable: a failed prewarm only means the first real
            # request may pay the model load cost.
            self._debug(f"llm warmup failed error={type(error).__name__}")
            self.output("Assistente será carregado na primeira solicitação.")
            return
        elapsed = self.monotonic_clock() - started
        self._debug(f"llm warmup completed seconds={elapsed:.2f}")
        self.output("Assistente pronto.")

    def _emit_wake_miss_telemetry(self) -> None:
        detector = getattr(self.listener, "detector", None)
        if detector is None:
            return

        base_score = getattr(detector, "last_base_score", 0.0)
        verifier_score = getattr(detector, "last_verifier_score", None)

        # Only log if it's a detectable attempt (e.g. verifier was engaged)
        if verifier_score is not None:
            now = self.monotonic_clock()
            if now - self._last_wake_miss_time > 1.0:
                self._last_wake_miss_time = now
                self._diagnostic_output(
                    f"[wake_miss] state={self.state.name} "
                    f"base={base_score:.3f} verifier={verifier_score:.3f} "
                    f"consecutive={getattr(detector, 'consecutive_detections', 0)} "
                    f"device={getattr(self.audio_session, 'device_index', 'N/A')} "
                )

    def process_once(self) -> bool:
        if self._stop_requested.is_set():
            return False
        self._drain_confirmation_events()
        manual_activation = self.ptt.pending
        event = None if manual_activation else self.listener.get_event()
        if not manual_activation and event is None and not self.ptt.pending:
            self._emit_wake_miss_telemetry()
            return False
        manual_activation = manual_activation or self.ptt.pending
        self._activation_origin = "ptt" if manual_activation else "wake_word"
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
        if self.diagnostic is not None:
            self.diagnostic.new_turn()
            self.diagnostic.record(
                "wake_accepted" if not manual_activation else "ptt_accepted",
                score=score, confirmed_frames=frames,
            )

        self.cancellation.clear()
        self._end_conversation = False
        if self.conversation_followup_enabled and not manual_activation:
            self.resource_manager.conversation()
        else:
            self.resource_manager.activate()
        wake_state_reset = False
        try:
            wake_state_reset = False if manual_activation else self._acknowledge_wake()
            capture = self._process_recording(self.recorder)
            wake_state_reset = wake_state_reset or capture.spoke
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
            allow_followup = self.conversation_followup_enabled and not manual_activation
            while allow_followup:
                if (
                    self.cancellation.is_cancelled or self.ptt.pending
                    or self._stop_requested.is_set()
                ):
                    break
                question = self._response_expects_answer()
                followup = self._followup_recorder(question=question)
                if followup is None:
                    break
                timeout, reason = self._followup_timing(question=question)
                self.resource_manager.conversation()
                self._set_state(VoicePipelineState.WAITING_FOLLOWUP)
                self._debug(
                    f"followup window started timeout={timeout:.1f} "
                    f"reason={reason}"
                )
                self.output("Ouvindo continuacao...")
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
                    if capture.status is VoiceCaptureStatus.TIMEOUT:
                        self._debug("followup timeout expired")
                    break
                wake_state_reset = self._process_pending_barge_in(
                    wake_state_reset
                )
            return True
        finally:
            if not wake_state_reset:
                self.listener.reset()
            if self._has_pending_confirmation():
                self.resource_manager.confirmation()
                self._set_state(VoicePipelineState.WAITING_CONFIRMATION)
            else:
                self.resource_manager.idle()
                self._set_state(VoicePipelineState.WAITING_WAKE)
            self.cancellation.clear()

    def _process_recording(
        self,
        recorder: UtteranceRecorder,
        announce: bool = True,
        empty_status: VoiceCaptureStatus = VoiceCaptureStatus.NO_SPEECH,
        profile: TranscriptionProfile | None = None,
        confirmation_context: tuple[str, str] | None = None,
    ) -> VoiceCaptureResult:
        capture_started = self.monotonic_clock()
        if self.diagnostic is not None:
            self.diagnostic.new_capture()
            self.diagnostic.record("capture_started", primary=announce)
        if confirmation_context is None:
            confirmation_context = self._confirmation_context()
        if announce:
            self._set_state(VoicePipelineState.LISTENING)
            self.output("Ouvindo...")
        if self.audio_session is None:
            self.ptt.begin_capture()
        if isinstance(recorder, UtteranceRecorder):
            recorder.diagnostic_audio_enabled = bool(
                announce and self.diagnostic is not None
                and self.diagnostic.saved_wavs < self.diagnostic.wav_limit
            )
        control = self.ptt if isinstance(recorder, UtteranceRecorder) else None
        try:
            if self.audio_session is not None:
                audio = self.audio_session.capture("utterance", recorder, control)
            else:
                audio = (
                    recorder.record(control=control)
                    if control is not None else recorder.record()
                )
        except Exception as error:
            self._trace("capture_error", {
                "error_type": type(error).__name__,
                "reason_code": NoSpeechReason.DEVICE_ERROR.value,
            })
            self.output(f"Erro ao capturar áudio: {type(error).__name__}")
            return VoiceCaptureResult(
                VoiceCaptureStatus.INTERRUPTED,
                reason=NoSpeechReason.DEVICE_ERROR.value,
            )
        finally:
            if isinstance(recorder, UtteranceRecorder):
                recorder.diagnostic_audio_enabled = False
            if self.audio_session is None:
                self.ptt.finish_capture()
        stop_reason = getattr(recorder, "last_stop_reason", None)
        self._trace("capture_ended", {
            "primary": announce,
            "duration_seconds": len(audio) / self.sample_rate,
            "elapsed_seconds": self.monotonic_clock() - capture_started,
            "stop_reason": stop_reason,
            "samples": len(audio),
        })
        if announce and self.diagnostic is not None:
            raw_audio = getattr(recorder, "last_diagnostic_audio", None)
            diagnostic_audio = audio if audio else (raw_audio or [])
            self.diagnostic.save_wav(
                diagnostic_audio, self.sample_rate,
                source="stt_input" if audio else "raw_no_speech",
            )
            if isinstance(recorder, UtteranceRecorder):
                recorder.last_diagnostic_audio = None
        if self._stop_requested.is_set():
            return VoiceCaptureResult(
                VoiceCaptureStatus.INTERRUPTED,
                reason=NoSpeechReason.CANCELLED.value,
            )
        if not audio:
            reason_code = self._no_speech_reason(recorder, stop_reason)
            self._trace("capture_classification", {
                "status": empty_status.name,
                "reason": stop_reason or "empty_audio",
                "reason_code": reason_code,
            })
            stop_reason = getattr(recorder, "last_stop_reason", None)
            if stop_reason:
                self._debug(f"recorder stop_reason={stop_reason}")
            noise_floor = getattr(recorder, "last_noise_floor", None)
            speech_threshold = getattr(recorder, "last_speech_threshold", None)
            if isinstance(noise_floor, (int, float)) and isinstance(
                speech_threshold, (int, float)
            ):
                self._debug(
                    "recorder noise_floor="
                    f"{float(noise_floor):.1f} "
                    "speech_threshold="
                    f"{float(speech_threshold):.1f}"
                )
            self._debug(f"recorder result={empty_status.name}")
            if announce:
                self.output(
                    "Nenhuma fala foi processada "
                    f"(motivo: {reason_code})."
                )
            return VoiceCaptureResult(empty_status, reason=reason_code)
        self._current_turn_started_at = self.monotonic_clock()
        self._current_stt_seconds = 0.0
        self._last_tts_total_seconds = 0.0
        self._last_harness_seconds = 0.0
        self._set_state(VoicePipelineState.PROCESSING)
        profile = profile or (
            TranscriptionProfile.GENERAL_COMMAND if announce
            else TranscriptionProfile.FOLLOWUP
        )
        captured_ms = len(audio) / 16.0
        is_short = captured_ms < 4000.0
        if is_short and self.diagnostic is not None:
            try:
                pre_roll = getattr(recorder, "pre_roll_seconds", 0.0)
                post_roll = getattr(recorder, "end_silence_seconds", 0.0)
                noise = getattr(recorder, "last_noise_floor", 0.0)
                thresh = getattr(recorder, "last_speech_threshold", 0.0)
                stop = getattr(recorder, "last_stop_reason", "N/A")
                clip = any(abs(x) >= 32000 for x in audio) if audio else False
                meta_audio = [
                    f"profile={profile.name if profile else 'N/A'}",
                    f"capture_ms={captured_ms:.0f}",
                    f"pre_roll={(pre_roll*1000.0 if isinstance(pre_roll, (int, float)) else 0.0):.0f}",
                    f"post_roll={(post_roll*1000.0 if isinstance(post_roll, (int, float)) else 0.0):.0f}",
                    f"noise={noise:.1f}",
                    f"thresh={thresh:.1f}",
                    f"stop={stop}",
                    f"clip={clip}"
                ]
                self._diagnostic_output(f"[short_audio] {' '.join(meta_audio)}")
            except Exception as e:
                self._debug(f"short_audio telemetry error: {e}")
        self._debug(f"speech gate=STARTED samples={len(audio)}")
        stop_reason = getattr(recorder, "last_stop_reason", None)
        if stop_reason:
            self._debug(f"recorder stop_reason={stop_reason}")
        try:
            result, validation = self._transcribe_and_validate(
                audio, profile
            )
        except Exception as error:
            self.output(f"Erro ao transcrever áudio: {error}")
            self._debug("stt result=INTERRUPTED")
            return VoiceCaptureResult(VoiceCaptureStatus.INTERRUPTED)
        from jarvis.integrations.google.drive_docs.routing import looks_private_document
        if self.stt_candidate_shadow is not None and not looks_private_document(result.text):
            try:
                self.stt_candidate_shadow.submit(
                    audio, self.sample_rate, result.text
                )
            except Exception as error:
                self._debug(
                    "stt candidate shadow failed "
                    f"error={type(error).__name__}"
                )
        if is_short and self.diagnostic is not None:
            try:
                dur = getattr(result, "duration_after_vad_seconds", 0.0) or 0.0
                lp = getattr(result, "average_log_probability", 0.0) or 0.0
                nsp = getattr(result, "no_speech_probability", 0.0) or 0.0
                langp = getattr(result, "language_probability", 0.0) or 0.0
                meta_stt = [
                    "text='<private-google>'" if looks_private_document(result.text) else f"text='{result.text.strip()}'",
                    f"speech_ms={dur*1000.0:.0f}",
                    f"avg_logprob={lp:.2f}",
                    f"no_speech={nsp:.2f}",
                    f"lang_prob={langp:.2f}"
                ]
                self._diagnostic_output(f"[short_stt] {' '.join(meta_stt)}")
            except Exception as e:
                self._debug(f"short_stt telemetry error: {e}")

        # Preserve the spoken form for security-sensitive target resolution.
        # FileReferenceResolver applies path normalization with risk context.
        text = result.text.strip()
        if not validation.accepted:
            self._trace("capture_classification", {
                "status": "TRANSCRIPTION_REJECTED",
                "reason": validation.reason,
            })
            self._debug_transcription_rejection(
                result, profile, validation.reason
            )
            return VoiceCaptureResult(
                VoiceCaptureStatus.TRANSCRIPTION_REJECTED,
                repairable=(
                    validation.quality is TranscriptionQuality.BORDERLINE
                ),
                reason=(
                    NoSpeechReason.EMPTY_TRANSCRIPTION.value
                    if not text else validation.reason
                ),
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
            self._cancel_pending_confirmation()
            self.output(f"Você [cancelamento]: {text}")
            self.output("Ação cancelada.")
            if self.conversation_log_bus is not None:
                self.conversation_log_bus.add_user(text)
                self.conversation_log_bus.add_assistant("Ação cancelada.")
            return VoiceCaptureResult(VoiceCaptureStatus.USER_CANCELLED)
        if self.conversation_end_interpreter.should_end(text):
            self._cancel_pending_confirmation()
            self.output(f"Você: {text}")
            self._end_conversation = True
            farewell = self.conversation_end_interpreter.reply(text)
            self.output(f"Jarvis: {farewell}")
            if self.conversation_log_bus is not None:
                self.conversation_log_bus.add_user(text)
                self.conversation_log_bus.add_assistant(farewell)
            spoke = self._speak_response(farewell)
            return VoiceCaptureResult(
                VoiceCaptureStatus.INTERRUPTED, spoke=spoke
            )
        processed, spoke = self._process_text(text, confirmation_context)
        self._trace("capture_classification", {
            "status": "SUCCESS" if processed else "INTERRUPTED",
            "reason": validation.reason,
        })
        return VoiceCaptureResult(
            VoiceCaptureStatus.SUCCESS if processed
            else VoiceCaptureStatus.INTERRUPTED,
            spoke=spoke,
        )

    @staticmethod
    def _no_speech_reason(recorder: object, stop_reason: str | None) -> str:
        frames = getattr(recorder, "last_frames_received", None)
        speech_samples = getattr(recorder, "last_speech_samples", 0)
        average_energy = getattr(recorder, "last_average_speech_energy", 0.0)
        if frames == 0 or (frames is None and stop_reason is None):
            return NoSpeechReason.NO_FRAMES.value
        if stop_reason == "pre_speech_timeout":
            max_energy = getattr(recorder, "last_max_frame_energy", 0.0)
            if not speech_samples and not average_energy and max_energy == 0:
                return NoSpeechReason.SILENT_FRAMES.value
            return NoSpeechReason.PRE_SPEECH_TIMEOUT.value
        if stop_reason == "speech_rejected":
            return NoSpeechReason.SPEECH_TOO_SHORT.value
        if stop_reason == "ptt_release":
            return NoSpeechReason.EARLY_TERMINATION.value
        return NoSpeechReason.UNKNOWN.value

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

    def _process_text(
        self,
        text: str,
        confirmation_context: tuple[str, str] | None = None,
    ) -> tuple[bool, bool]:
        self._set_state(VoicePipelineState.PROCESSING)
        from jarvis.integrations.google.drive_docs.routing import looks_private_document
        private_document = looks_private_document(text)
        from jarvis.application.delegation import looks_delegation
        private_delegation = looks_delegation(text)
        self.output('Você: <delegação privada>' if private_delegation else
                    'Você: <consulta privada Google>' if private_document else f"Você: {text}")
        if self.conversation_log_bus is not None:
            self.conversation_log_bus.add_user(text)
        if not private_document and not private_delegation:
            self._observe_semantic_shadow(text)
        self._acknowledge(text)
        harness_started = self.monotonic_clock()
        session_id = (
            confirmation_context[0] if confirmation_context is not None else None
        )
        challenge_id = (
            confirmation_context[1] if confirmation_context is not None else None
        )
        harness_arguments = {
            "manage_runtime": False,
            "response_mode": "voice",
        }
        if session_id is not None and challenge_id is not None:
            harness_arguments["confirmation_session_id"] = session_id
            harness_arguments["confirmation_challenge_id"] = challenge_id
        response = self.harness.processar_mensagem(text, **harness_arguments)
        private_calendar = (getattr(self.harness, "last_response_private_calendar", False)
                            or getattr(self.harness, "last_response_private_google", False))
        self._trace("response_formatted", {"text": "<private-calendar>" if private_calendar else response})
        self._last_harness_seconds = (
            self.monotonic_clock() - harness_started
        )
        self._last_response = response
        confirmation_state = self._confirmation_state()
        if confirmation_state == "waiting_confirmation":
            self._set_state(VoicePipelineState.REQUEST_CONFIRMATION)
        elif confirmation_state == "confirmed":
            self._set_state(VoicePipelineState.CONFIRMED)
        elif confirmation_state == "denied":
            self._set_state(VoicePipelineState.DENIED)
        elif confirmation_state == "expired":
            self._set_state(VoicePipelineState.EXPIRED)
        self.output("Jarvis: <agenda privada na interface local>" if private_calendar else f"Jarvis: {response}")
        if self.conversation_log_bus is not None:
            self.conversation_log_bus.add_assistant(
                getattr(self.harness, 'last_briefing_text', '') or response)
        spoke = self._speak_response(response)
        self._emit_latency_report(spoke=spoke)
        return True, spoke

    def _observe_semantic_shadow(self, text: str) -> None:
        observer = self.semantic_shadow
        if observer is None:
            return
        try:
            observer.observe(text, activation_origin=self._activation_origin)
        except Exception as error:
            self._debug(
                "semantic shadow observation failed "
                f"error={type(error).__name__}"
            )

    def _acknowledge_wake(self) -> bool:
        if not self.wake_acknowledgement_enabled or not self._tts_is_allowed():
            return False
        text_to_speech = self.text_to_speech
        if text_to_speech is None or not getattr(
            text_to_speech, "fast_acknowledgement", True
        ):
            return False
        phrase = self.phrase_manager.wake().strip()
        if not phrase:
            return False
        self.output(f"Jarvis: {phrase}")
        return self._speak_response(phrase)

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
        if not getattr(text_to_speech, "fast_acknowledgement", True):
            return
        phrase = self.phrase_manager.action_acknowledgement(text)
        if not phrase:
            return
        if self.audio_session is not None:
            self.audio_session.stop_stream()
        else:
            self.listener.stop()
        try:
            self.output(f"Jarvis: {phrase}")
            text_to_speech.speak(phrase)
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
            _, spoke = self._process_text(text, self._confirmation_context())
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

    def _followup_timing(self, *, question: bool) -> tuple[float, str]:
        if self.resource_manager.is_game_mode_active():
            return self.game_mode_followup_timeout_seconds, "game_mode"
        if question and self.question_followup_recorder is not None:
            return self.question_followup_timeout_seconds, "assistant_question"
        return self.followup_timeout_seconds, "normal"

    def _response_expects_answer(self) -> bool:
        pending = getattr(self.harness, "has_pending_action", None)
        if callable(pending) and pending():
            return True
        return "?" in self._last_response

    def _enter_waiting_wake(self, *, announce: bool) -> None:
        if self._has_pending_confirmation():
            self.resource_manager.confirmation()
            self._set_state(VoicePipelineState.WAITING_CONFIRMATION)
            return
        if self.state is VoicePipelineState.WAITING_WAKE and not announce:
            return
        self._set_state(VoicePipelineState.WAITING_WAKE)
        if announce:
            self.output(f'Aguardando "{self.wake_word_label}"...')

    def close(self) -> None:
        if self._closed:
            return
        self.ptt.close()
        try:
            self.listener.close()
        finally:
            try:
                self.speech_to_text.close()
            finally:
                if self.transcription_fallback is not None:
                    self.transcription_fallback.close()
                if self.text_to_speech is not None:
                    self.text_to_speech.close()
                if self.semantic_shadow is not None:
                    self.semantic_shadow.close()
                if self.stt_candidate_shadow is not None:
                    self.stt_candidate_shadow.close()
                close_confirmations = getattr(
                    self.harness, "close_confirmation_sessions", None
                )
                if callable(close_confirmations):
                    close_confirmations()
                self._closed = True

    def request_stop(self) -> None:
        """Stops accepting turns and unblocks an idle wake-word wait."""
        self._stop_requested.set()
        self.ptt.close()
        if self.state is VoicePipelineState.WAITING_WAKE:
            self.listener.close()

    def handle_ui_command(self, payload: dict[str, object]) -> None:
        """Only capture intent; never dispatch a Tool or grant authority."""
        if not isinstance(payload, dict) or set(payload) != {"command"}:
            return
        if payload["command"] == "ptt_start":
            self.ptt.press()
        elif payload["command"] == "ptt_stop":
            self.ptt.release()

    def set_confirmation_state(self, state: VoicePipelineState) -> None:
        if state in {
            VoicePipelineState.SPEAKING,
            VoicePipelineState.CONFIRMATION,
            VoicePipelineState.REQUEST_CONFIRMATION,
            VoicePipelineState.WAITING_CONFIRMATION,
            VoicePipelineState.CONFIRMED,
            VoicePipelineState.DENIED,
            VoicePipelineState.EXPIRED,
        }:
            self._set_state(state)

    def _has_pending_confirmation(self) -> bool:
        pending = getattr(self.harness, "has_pending_confirmation", None)
        return bool(callable(pending) and pending())

    def _confirmation_context(self) -> tuple[str, str] | None:
        current = getattr(self.harness, "current_confirmation_context", None)
        return current() if callable(current) else None

    def _confirmation_state(self) -> str | None:
        current = getattr(self.harness, "confirmation_state", None)
        return current() if callable(current) else None

    def _cancel_pending_confirmation(self) -> None:
        cancel = getattr(self.harness, "cancel_pending_confirmation", None)
        if callable(cancel):
            cancel()

    def _drain_confirmation_events(self) -> None:
        pop_event = getattr(self.harness, "pop_confirmation_event", None)
        if not callable(pop_event):
            return
        while True:
            event = pop_event()
            if event is None:
                return
            kind, reminder_index, session_id = event
            current = self._confirmation_context()
            if current is not None and current[0] != session_id:
                continue
            if kind == "reminder":
                if current is None:
                    continue
                phrase = self.phrase_manager.confirmation_reminder(
                    reminder_index
                )
                if phrase:
                    self.output(f"Jarvis: {phrase}")
                    self._speak_response(phrase)
                self.resource_manager.confirmation()
                self._set_state(VoicePipelineState.WAITING_CONFIRMATION)
            elif kind == "expired":
                self._set_state(VoicePipelineState.EXPIRED)
                phrase = "A confirmação expirou. A ação não foi realizada."
                self.output(f"Jarvis: {phrase}")
                self._speak_response(phrase)

    def _speak_response(self, response: str) -> bool:
        ownership = (
            self.audio_session.ownership("playback")
            if self.audio_session is not None else nullcontext()
        )
        with ownership:
            return self._speak_response_owned(response)

    def _speak_response_owned(self, response: str) -> bool:
        if (
            not self._tts_is_allowed() or self.ptt.pending
            or self._stop_requested.is_set()
        ):
            return False

        spoken_text = prepare_text_for_speech(response)
        if not spoken_text:
            return False

        self._set_state(VoicePipelineState.SPEAKING)
        if self.audio_session is not None:
            self.audio_session.stop_stream()
        else:
            self.listener.stop()
        playback = None
        resume_input = True
        try:
            try:
                text_to_speech = self.text_to_speech
                if text_to_speech is not None:
                    self._debug("tts started")
                    tts_started = self.monotonic_clock()
                    playback = text_to_speech.speak_async(spoken_text)
                    # No AEC: input stays stopped throughout ordinary playback.
                    # IPC only latches intent; this worker stops and joins TTS
                    # before transferring microphone ownership to capture.
                    while not playback.wait(0.02):
                        if self.ptt.pending or self._stop_requested.is_set():
                            playback.stop()
                            playback.wait()
                            break
                    self._last_tts_total_seconds = (
                        self.monotonic_clock() - tts_started
                    )
                    self._debug("tts finished")
            except Exception as error:
                if playback is not None and not playback.finished:
                    # A failed stop must never reopen input over live output.
                    resume_input = False
                    raise RuntimeError("Não foi possível encerrar o playback.") from error
                self.output(
                    "Aviso: não foi possível reproduzir a resposta por voz."
                )
                self.output(
                    f"Falha no TTS: {type(error).__name__}: {error}"
                )
        finally:
            # A janela de follow-up só poderá ser iniciada pelo chamador
            # depois que playback e cooldown terminarem neste bloco.
            if not self.ptt.pending and not self._stop_requested.is_set():
                self.cooldown_waiter(self.post_speech_cooldown_seconds)
            self.listener.reset()
            if resume_input and not self._stop_requested.is_set():
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
        return self.transcription_coordinator.is_reliable_control_transcription(result)

    def _transcribe_and_validate(
        self,
        audio: list[int],
        profile: TranscriptionProfile,
    ) -> tuple[object, TranscriptionValidation]:
        result, validation, elapsed = self.transcription_coordinator.transcribe_and_validate(
            audio, profile
        )
        self._current_stt_seconds += elapsed
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
        return self.transcription_coordinator.validate(result, audio, profile)

    def _debug_transcription_rejection(
        self,
        result: object,
        profile: TranscriptionProfile | None = None,
        reason: str = "rejected",
    ) -> None:
        self.transcription_coordinator.debug_rejection(result, profile, reason)

    def _debug_transcription_accepted(
        self,
        result: object,
        profile: TranscriptionProfile,
    ) -> None:
        self.transcription_coordinator.debug_accepted(result, profile)

    def _emit_latency_report(self, *, spoke: bool) -> None:
        if not self.latency_debug_enabled:
            return
        get_latency = getattr(self.harness, "get_last_latency", None)
        harness_latency = get_latency() if callable(get_latency) else {}
        llm_seconds = float(harness_latency.get("llm_seconds", 0.0))
        llm_calls = int(harness_latency.get("llm_calls", 0))
        tool_seconds = float(harness_latency.get("tool_seconds", 0.0))
        tool_calls = int(harness_latency.get("tool_calls", 0))
        harness_seconds = float(
            harness_latency.get("harness_seconds", self._last_harness_seconds)
        )
        tts_synthesis = None
        if spoke and self.text_to_speech is not None:
            value = getattr(
                self.text_to_speech, "last_synthesis_seconds", None
            )
            if isinstance(value, (int, float)):
                tts_synthesis = float(value)
        to_voice = self._current_stt_seconds + harness_seconds
        if tts_synthesis is not None:
            to_voice += tts_synthesis
        tool_time = self._format_short_duration(tool_seconds, tool_calls)
        parts = [
            f"STT={self._current_stt_seconds:.2f}s",
            f"Harness={harness_seconds:.2f}s",
            f"LLM={llm_seconds:.2f}s/{llm_calls}",
            f"Tools={tool_time}/{tool_calls}",
        ]
        if tts_synthesis is not None:
            parts.append(f"TTS-synth={tts_synthesis:.2f}s")
            parts.append(f"ate-voz~{to_voice:.2f}s")
        elif spoke:
            parts.append(f"TTS-total={self._last_tts_total_seconds:.2f}s")
        turn_started = self._current_turn_started_at
        if turn_started is not None:
            parts.append(
                f"turno-total={self.monotonic_clock() - turn_started:.2f}s"
            )
        self._diagnostic_output("[latency] " + " | ".join(parts))

    def _diagnostic_output(self, message: str) -> None:
        """Emit debug/telemetry output without risking the voice runtime."""
        try:
            self.output(message)
        except Exception:
            return

    @staticmethod
    def _format_short_duration(seconds: float, calls: int) -> str:
        if calls > 0 and 0.0 <= seconds < 0.01:
            return f"{seconds * 1000:.1f}ms"
        return f"{seconds:.2f}s"

    def _debug(self, message: str) -> None:
        if self.debug_enabled:
            self._diagnostic_output(
                f"[voice] t={self.monotonic_clock():.3f} {message}"
            )

    def _set_state(self, state: VoicePipelineState) -> None:
        previous = self.state
        self.state = state
        if previous is not state:
            self._debug(f"transition={previous.name} -> {state.name}")
            callback = self.state_callback
            if callback is not None:
                try:
                    callback(state)
                except Exception as error:
                    self._debug(
                        "state callback failed error="
                        f"{type(error).__name__}"
                    )

    def _tts_is_allowed(self) -> bool:
        enabled = getattr(getattr(self.text_to_speech, "config", None), "tts_enabled", self.tts_enabled)
        if self.text_to_speech is None or not enabled:
            return False
        return (
            self.tts_enabled_game_mode
            or not self.resource_manager.is_game_mode_active()
        )
