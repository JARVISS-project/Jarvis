import argparse
from pathlib import Path

from jarvis.activation.listener import WakeWordListener
from jarvis.activation.openwakeword import OpenWakeWordDetector
from jarvis.audio.input import PvRecorderAudioInput
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.config import JarvisConfig
from jarvis.context.file_reference_resolver import FileReferenceResolver
from jarvis.context.recent import RecentContext
from jarvis.context.note_reference_resolver import NoteReferenceResolver
from jarvis.harness import Harness
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.llm.ollama_client import OllamaLLM
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.sqlite_store import SQLiteMemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.cancellation import CancellationController
from jarvis.security.audit import AuditLogger
from jarvis.security.confirmation import ConfirmationProvider, VoiceConfirmation
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.safety_manager import SafetyManager
from jarvis.speech.faster_whisper import FasterWhisperSTT
from jarvis.speech.normalizer import TranscriptNormalizer
from jarvis.speech.validator import TranscriptionValidator
from jarvis.speech.context import SpeechContextBuilder
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.edit_file import EditFileTool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.get_datetime import GetDateTimeTool
from jarvis.tools.get_runtime_info import GetRuntimeInfoTool
from jarvis.tools.list_files import ListFilesTool
from jarvis.tools.move_file import MoveFileTool
from jarvis.tools.obsidian import obsidian_tools
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.registry import ToolRegistry
from jarvis.tts.chatterbox import ChatterboxTTS
from jarvis.tts.piper import PiperTTS
from jarvis.tts.piper_setup import setup_piper_voice
from jarvis.tts.factory import build_text_to_speech
from jarvis.voice.pipeline import VoicePipeline
from jarvis.voice.barge_in import BargeInController
from jarvis.voice.audio_session import VoiceAudioSession


def build_harness(
    config: JarvisConfig,
    resource_manager: ResourceManager,
    confirmation: ConfirmationProvider | None = None,
    recent_context: RecentContext | None = None,
) -> Harness:
    llm = OllamaLLM(
        model=config.model_name,
        keep_alive=config.ollama_keep_alive_normal,
        thinking_enabled=config.llm_thinking_enabled,
    )
    safety_manager = SafetyManager(confirmation=confirmation)
    capability_policy = CapabilityPolicy(
        {
            Capability.FILESYSTEM_READ: config.filesystem_read_enabled,
            Capability.FILESYSTEM_WRITE: config.filesystem_write_enabled,
            Capability.OBSIDIAN_READ: config.obsidian_read_enabled,
            Capability.OBSIDIAN_WRITE: config.obsidian_write_enabled,
        }
    )
    audit_logger = AuditLogger(config.audit_log_path)
    tool_executor = ToolExecutor(
        safety_manager=safety_manager,
        audit_logger=audit_logger,
        capability_policy=capability_policy,
    )
    filesystem = FileSystemGuard(
        base_directory=config.filesystem_base_directory
    )
    memory = ConversationMemory(
        max_messages=config.conversation_max_messages
    )
    memory_store = SQLiteMemoryStore(config.database_path)
    recent_context = recent_context or RecentContext()
    file_reference_resolver = FileReferenceResolver(
        filesystem,
        recent_context,
    )

    tool_registry = ToolRegistry()
    tool_registry.register(GetDateTimeTool())
    tool_registry.register(GetRuntimeInfoTool(config, resource_manager))
    if capability_policy.is_enabled(Capability.FILESYSTEM_READ):
        tool_registry.register(ListFilesTool(filesystem))
        tool_registry.register(ReadFileTool(filesystem))
    if capability_policy.is_enabled(Capability.FILESYSTEM_WRITE):
        tool_registry.register(CreateFileTool(filesystem))
        tool_registry.register(EditFileTool(filesystem))
        tool_registry.register(MoveFileTool(filesystem))
        tool_registry.register(DeleteFileTool(filesystem))

    note_reference_resolver = None
    if (
        config.obsidian_enabled
        and config.obsidian_vault_path is not None
        and config.obsidian_vault_path.is_dir()
    ):
        obsidian_filesystem = FileSystemGuard(
            base_directory=config.obsidian_vault_path
        )
        obsidian_vault = ObsidianVault(
            vault_path=config.obsidian_vault_path,
            filesystem=obsidian_filesystem,
            max_note_size_bytes=config.obsidian_max_note_size_bytes,
            search_max_results=config.obsidian_search_max_results,
        )
        note_reference_resolver = NoteReferenceResolver(
            obsidian_vault, recent_context
        )
        for tool in obsidian_tools(obsidian_vault):
            capability = (
                Capability.OBSIDIAN_WRITE
                if tool.name in {
                    "obsidian_create_note", "obsidian_append_note",
                    "obsidian_delete_note",
                }
                else Capability.OBSIDIAN_READ
            )
            if capability_policy.is_enabled(capability):
                tool_registry.register(tool)

    return Harness(
        llm=llm,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        memory=memory,
        memory_store=memory_store,
        config=config,
        resource_manager=resource_manager,
        recent_context=recent_context,
        file_reference_resolver=file_reference_resolver,
        target_confirmation=safety_manager.confirmation,
        note_reference_resolver=note_reference_resolver,
    )


def run_voice_mode(
    config: JarvisConfig,
    model_path: Path | None = None,
    microphone_device_index: int | None = None,
) -> None:
    resource_manager = ResourceManager()
    resource_manager.idle()
    cancellation = CancellationController()
    recent_context = RecentContext()
    speech_context = SpeechContextBuilder(recent_context)

    detector = OpenWakeWordDetector(
        model_name=config.wake_word_model,
        model_path=model_path or config.wake_word_model_path,
        threshold=config.wake_word_threshold,
        minimum_consecutive_detections=(
            config.wake_min_consecutive_detections
        ),
    )
    try:
        audio_input = PvRecorderAudioInput(
            frame_length=detector.frame_length,
            device_index=(
                microphone_device_index
                if microphone_device_index is not None
                else config.microphone_device_index
            ),
        )
    except Exception:
        detector.close()
        raise

    listener = WakeWordListener(
        audio_input,
        detector,
        config.wake_word_cooldown_seconds,
    )
    audio_session = VoiceAudioSession(listener)
    recorder = UtteranceRecorder(
        audio_input=audio_input,
        sample_rate=detector.sample_rate,
        silence_threshold=config.voice_silence_threshold,
        silence_duration_seconds=config.voice_silence_duration_seconds,
        max_recording_seconds=config.voice_max_recording_seconds,
        pre_speech_timeout_seconds=config.voice_pre_speech_timeout_seconds,
        minimum_speech_duration_seconds=(
            config.voice_minimum_speech_duration_seconds
        ),
        minimum_audio_energy=config.voice_minimum_audio_energy,
        noise_calibration_seconds=config.voice_noise_calibration_seconds,
        noise_margin=config.voice_noise_margin,
        maximum_speech_threshold=config.voice_maximum_speech_threshold,
        pre_roll_seconds=config.voice_pre_roll_seconds,
        end_silence_seconds=config.voice_end_silence_seconds,
    )

    def stt_runtime_options() -> tuple[str, str, str]:
        if resource_manager.is_game_mode_active():
            return (
                config.stt_model_game_mode,
                config.stt_device_game_mode,
                config.stt_compute_type_game_mode,
            )
        return (
            config.stt_model_normal,
            config.stt_device_normal,
            config.stt_compute_type_normal,
        )

    speech_to_text = FasterWhisperSTT(
        model_name=config.stt_model,
        language=config.stt_language,
        runtime_options=stt_runtime_options,
        initial_prompt=config.stt_initial_prompt,
        hotwords_provider=speech_context.build_hotwords,
        hotwords_enabled=config.stt_hotwords_enabled,
        vad_enabled=config.stt_vad_enabled,
        vad_min_silence_duration_ms=(
            config.stt_vad_min_silence_duration_ms
        ),
        vad_speech_pad_ms=config.stt_vad_speech_pad_ms,
        vad_threshold=config.stt_vad_threshold,
    )
    text_to_speech = build_text_to_speech(config)
    def confirmation_recorder(timeout_seconds: float) -> UtteranceRecorder:
        return UtteranceRecorder(
            audio_input=audio_input,
            sample_rate=detector.sample_rate,
            silence_threshold=config.voice_silence_threshold,
            silence_duration_seconds=min(
                config.voice_silence_duration_seconds, 0.6
            ),
            max_recording_seconds=timeout_seconds,
            pre_speech_timeout_seconds=timeout_seconds,
            minimum_speech_duration_seconds=(
                config.voice_confirmation_min_speech_duration_seconds
            ),
            minimum_audio_energy=config.voice_minimum_audio_energy,
            noise_calibration_seconds=config.voice_noise_calibration_seconds,
            noise_margin=config.voice_noise_margin,
            maximum_speech_threshold=config.voice_maximum_speech_threshold,
            pre_roll_seconds=config.voice_pre_roll_seconds,
            end_silence_seconds=min(config.voice_end_silence_seconds, 0.6),
        )
    confirmation = (
        VoiceConfirmation(
            listener=listener,
            recorder=confirmation_recorder(
                config.voice_confirmation_timeout_seconds
            ),
            retry_recorder=confirmation_recorder(
                config.voice_confirmation_retry_timeout_seconds
            ),
            speech_to_text=speech_to_text,
            text_to_speech=text_to_speech,
            resource_manager=resource_manager,
            sample_rate=detector.sample_rate,
            keyboard_fallback=(
                config.voice_confirmation_keyboard_fallback
            ),
            cancellation=cancellation,
            audio_session=audio_session,
            post_speech_cooldown_seconds=(
                config.voice_post_tts_grace_seconds
            ),
            debug_enabled=config.voice_debug_enabled,
        )
        if config.voice_confirmation_enabled
        else None
    )
    def followup_recorder(timeout_seconds: float) -> UtteranceRecorder:
        return UtteranceRecorder(
            audio_input=audio_input,
            sample_rate=detector.sample_rate,
            silence_threshold=config.voice_silence_threshold,
            silence_duration_seconds=config.voice_silence_duration_seconds,
            max_recording_seconds=timeout_seconds,
            pre_speech_timeout_seconds=timeout_seconds,
            minimum_speech_duration_seconds=(
                config.voice_minimum_speech_duration_seconds
            ),
            minimum_audio_energy=config.voice_minimum_audio_energy,
            noise_calibration_seconds=config.voice_noise_calibration_seconds,
            noise_margin=config.voice_noise_margin,
            maximum_speech_threshold=config.voice_maximum_speech_threshold,
            pre_roll_seconds=config.voice_pre_roll_seconds,
            end_silence_seconds=config.voice_end_silence_seconds,
        )

    def incomplete_turn_recorder() -> UtteranceRecorder:
        # ``voice_incomplete_turn_extension_seconds`` e a janela para o usuario
        # voltar a falar. Depois que a continuacao comeca, ela pode durar ate
        # o limite normal; antes, o mesmo valor tambem limitava a gravacao.
        return UtteranceRecorder(
            audio_input=audio_input,
            sample_rate=detector.sample_rate,
            silence_threshold=config.voice_silence_threshold,
            silence_duration_seconds=config.voice_silence_duration_seconds,
            max_recording_seconds=config.voice_max_recording_seconds,
            pre_speech_timeout_seconds=(
                config.voice_incomplete_turn_extension_seconds
            ),
            minimum_speech_duration_seconds=(
                config.voice_minimum_speech_duration_seconds
            ),
            minimum_audio_energy=config.voice_minimum_audio_energy,
            noise_calibration_seconds=config.voice_noise_calibration_seconds,
            noise_margin=config.voice_noise_margin,
            maximum_speech_threshold=config.voice_maximum_speech_threshold,
            pre_roll_seconds=config.voice_pre_roll_seconds,
            end_silence_seconds=config.voice_end_silence_seconds,
        )

    harness = build_harness(
        config, resource_manager, confirmation, recent_context
    )
    pipeline = VoicePipeline(
        listener=listener,
        recorder=recorder,
        speech_to_text=speech_to_text,
        harness=harness,
        resource_manager=resource_manager,
        text_to_speech=text_to_speech,
        tts_enabled=config.tts_enabled,
        tts_enabled_game_mode=config.tts_enabled_game_mode,
        post_speech_cooldown_seconds=(
            config.voice_post_tts_grace_seconds
        ),
        sample_rate=detector.sample_rate,
        transcription_validator=TranscriptionValidator(),
        transcript_normalizer=TranscriptNormalizer(),
        conversation_followup_enabled=config.conversation_followup_enabled,
        followup_recorder=followup_recorder(
            config.conversation_followup_timeout_seconds
        ),
        game_mode_followup_recorder=followup_recorder(
            config.conversation_followup_timeout_game_mode_seconds
        ),
        question_followup_recorder=followup_recorder(
            config.conversation_question_timeout_seconds
        ),
        repair_recorder=followup_recorder(
            config.voice_repair_timeout_seconds
        ),
        incomplete_turn_recorder=incomplete_turn_recorder(),
        cancellation=cancellation,
        audio_session=audio_session,
        acknowledgement_enabled=config.voice_acknowledgement_enabled,
        debug_enabled=config.voice_debug_enabled,
        latency_debug_enabled=config.latency_debug_enabled,
        cancel_max_no_speech_probability=(
            config.voice_cancel_max_no_speech_probability
        ),
        cancel_min_average_log_probability=(
            config.voice_cancel_min_average_log_probability
        ),
        cancel_min_speech_duration_seconds=(
            config.voice_cancel_min_speech_duration_seconds
        ),
        followup_timeout_seconds=(
            config.conversation_followup_timeout_seconds
        ),
        question_followup_timeout_seconds=(
            config.conversation_question_timeout_seconds
        ),
        game_mode_followup_timeout_seconds=(
            config.conversation_followup_timeout_game_mode_seconds
        ),
        barge_in_controller=(
            BargeInController(
                audio_input=audio_input,
                sample_rate=detector.sample_rate,
                energy_margin=config.barge_in_energy_margin,
                minimum_speech_duration_seconds=(
                    config.barge_in_min_speech_duration_seconds
                ),
                grace_period_seconds=config.barge_in_grace_period_seconds,
            )
            if config.barge_in_enabled and text_to_speech is not None
            else None
        ),
    )
    if confirmation is not None:
        state_callback = getattr(pipeline, "set_confirmation_state", None)
        if callable(state_callback):
            confirmation.bind_state_callback(state_callback)
    pipeline.run()


def setup_voice_model(model_name: str) -> None:
    try:
        import openwakeword
    except ImportError as error:
        raise RuntimeError(
            "Suporte de voz não instalado. Instale o extra 'voice'."
        ) from error

    openwakeword.utils.download_models(model_names=[model_name])


def setup_neural_voice(config: JarvisConfig) -> None:
    tts = ChatterboxTTS(
        device=config.tts_device,
        language_id=config.tts_language_id,
        reference_audio_path=config.tts_reference_audio_path,
        t3_model=config.tts_chatterbox_t3_model,
        exaggeration=config.tts_chatterbox_exaggeration,
        cfg_weight=config.tts_chatterbox_cfg_weight,
    )
    try:
        tts.warm_up()
    finally:
        tts.close()


def setup_fast_voice(config: JarvisConfig) -> None:
    try:
        import piper  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "Piper não instalado. Instale com: "
            "python -m pip install -e \".[fast-voice]\""
        ) from error

    model_path, config_path = setup_piper_voice(
        config.tts_piper_model_path,
        config.tts_piper_config_path,
    )
    tts = PiperTTS(
        model_path=model_path,
        config_path=config_path,
        tail_silence_seconds=config.tts_piper_tail_silence_seconds,
    )
    try:
        tts.warm_up()
    finally:
        tts.close()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Aguarda a wake word e processa comandos falados.",
    )
    parser.add_argument(
        "--wake-word-model-path",
        type=Path,
        help="Caminho opcional para um modelo ONNX local.",
    )
    parser.add_argument(
        "--setup-voice",
        action="store_true",
        help="Baixa uma vez o modelo pré-treinado de wake word.",
    )
    parser.add_argument(
        "--setup-neural-voice",
        action="store_true",
        help="Baixa e prepara o Chatterbox local para o TTS neural.",
    )
    parser.add_argument(
        "--setup-fast-voice",
        action="store_true",
        help="Baixa e prepara a voz rápida pt-BR do Piper.",
    )
    parser.add_argument(
        "--microphone-device-index",
        type=int,
        help="Índice opcional do dispositivo de entrada.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _argument_parser().parse_args(argv)
    config = JarvisConfig()

    if args.setup_voice:
        try:
            setup_voice_model(config.wake_word_model)
        except RuntimeError as error:
            raise SystemExit(f"Erro ao preparar modo voice: {error}") from error
        print("Modelo de wake word preparado.")
        return

    if args.setup_neural_voice:
        try:
            setup_neural_voice(config)
        except RuntimeError as error:
            raise SystemExit(
                f"Erro ao preparar TTS neural: {error}"
            ) from error
        print("TTS neural preparado.")
        return

    if args.setup_fast_voice:
        try:
            setup_fast_voice(config)
        except RuntimeError as error:
            raise SystemExit(
                f"Erro ao preparar TTS rápido: {error}"
            ) from error
        print("TTS rápido preparado.")
        return

    if args.voice or config.wake_word_enabled:
        try:
            run_voice_mode(
                config=config,
                model_path=args.wake_word_model_path,
                microphone_device_index=args.microphone_device_index,
            )
        except RuntimeError as error:
            raise SystemExit(f"Erro no modo voice: {error}") from error
        return

    resource_manager = ResourceManager()
    harness = build_harness(config, resource_manager)
    harness.run()


if __name__ == "__main__":
    main()
