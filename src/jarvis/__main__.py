import argparse
from pathlib import Path

from jarvis.activation.listener import WakeWordListener
from jarvis.activation.openwakeword import OpenWakeWordDetector
from jarvis.audio.input import PvRecorderAudioInput
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.ollama_client import OllamaLLM
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.sqlite_store import SQLiteMemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.security.audit import AuditLogger
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.safety_manager import SafetyManager
from jarvis.speech.faster_whisper import FasterWhisperSTT
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.edit_file import EditFileTool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.get_datetime import GetDateTimeTool
from jarvis.tools.list_files import ListFilesTool
from jarvis.tools.move_file import MoveFileTool
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.test_tool import TestTool
from jarvis.tts.windows_sapi import WindowsSapiTTS
from jarvis.voice.pipeline import VoicePipeline


def build_harness(
    config: JarvisConfig,
    resource_manager: ResourceManager,
) -> Harness:
    llm = OllamaLLM(
        model=config.model_name,
        keep_alive=config.ollama_keep_alive_normal,
        thinking_enabled=config.llm_thinking_enabled,
    )
    safety_manager = SafetyManager()
    audit_logger = AuditLogger(config.audit_log_path)
    tool_executor = ToolExecutor(
        safety_manager=safety_manager,
        audit_logger=audit_logger,
    )
    filesystem = FileSystemGuard(
        base_directory=config.filesystem_base_directory
    )
    memory = ConversationMemory(
        max_messages=config.conversation_max_messages
    )
    memory_store = SQLiteMemoryStore(config.database_path)

    tool_registry = ToolRegistry()
    tool_registry.register(TestTool())
    tool_registry.register(GetDateTimeTool())
    tool_registry.register(ListFilesTool(filesystem))
    tool_registry.register(ReadFileTool(filesystem))
    tool_registry.register(CreateFileTool(filesystem))
    tool_registry.register(EditFileTool(filesystem))
    tool_registry.register(MoveFileTool(filesystem))
    tool_registry.register(DeleteFileTool(filesystem))

    return Harness(
        llm=llm,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        memory=memory,
        memory_store=memory_store,
        config=config,
        resource_manager=resource_manager,
    )


def run_voice_mode(
    config: JarvisConfig,
    model_path: Path | None = None,
    microphone_device_index: int | None = None,
) -> None:
    resource_manager = ResourceManager()
    resource_manager.idle()
    harness = build_harness(config, resource_manager)

    detector = OpenWakeWordDetector(
        model_name=config.wake_word_model,
        model_path=model_path or config.wake_word_model_path,
        threshold=config.wake_word_threshold,
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
    )

    def stt_runtime_options() -> tuple[str, str]:
        if resource_manager.is_game_mode_active():
            return (
                config.stt_device_game_mode,
                config.stt_compute_type_game_mode,
            )
        return (
            config.stt_device_normal,
            config.stt_compute_type_normal,
        )

    speech_to_text = FasterWhisperSTT(
        model_name=config.stt_model,
        language=config.stt_language,
        runtime_options=stt_runtime_options,
    )
    text_to_speech = (
        WindowsSapiTTS(
            rate=config.tts_rate,
            volume=config.tts_volume,
            voice_id=config.tts_voice_id,
        )
        if config.tts_enabled
        else None
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
            config.tts_post_speech_cooldown_seconds
        ),
        sample_rate=detector.sample_rate,
    )
    pipeline.run()


def setup_voice_model(model_name: str) -> None:
    try:
        import openwakeword
    except ImportError as error:
        raise RuntimeError(
            "Suporte de voz não instalado. Instale o extra 'voice'."
        ) from error

    openwakeword.utils.download_models(model_names=[model_name])


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
