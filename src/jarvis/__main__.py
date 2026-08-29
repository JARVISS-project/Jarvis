import argparse
import atexit
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from jarvis.activation.listener import WakeWordListener
from jarvis.activation.openwakeword import OpenWakeWordDetector
from jarvis.application.state import AgentStateBus
from jarvis.application.telemetry import TelemetryBus
from jarvis.application.state_ipc import LocalStateServer
from jarvis.application.voice_bridge import VoiceStateBridge
from jarvis.application.tool_execution_state import AgentToolExecutionObserver
from jarvis.audio.input import PvRecorderAudioInput
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.desktop.factory import build_desktop_service
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
from jarvis.runtime.ambient_ui import (
    launch_ambient_ui,
    stop_spawned_ambient_ui,
)
from jarvis.runtime.cancellation import CancellationController
from jarvis.runtime.desktop_kill_switch import WindowsDesktopKillSwitchListener
from jarvis.security.audit import AuditLogger
from jarvis.security.confirmation import ConfirmationProvider, VoiceConfirmation
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.safety_manager import SafetyManager
from jarvis.skills.mirror import ObsidianSkillMirror
from jarvis.skills.store import JsonSkillStore
from jarvis.speech.faster_whisper import FasterWhisperSTT
from jarvis.speech.normalizer import TranscriptNormalizer
from jarvis.speech.validator import TranscriptionValidator
from jarvis.speech.context import SpeechContextBuilder
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.desktop import desktop_tools
from jarvis.tools.edit_file import EditFileTool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.get_datetime import GetDateTimeTool
from jarvis.tools.get_runtime_info import GetRuntimeInfoTool
from jarvis.tools.list_files import ListFilesTool
from jarvis.tools.move_file import MoveFileTool
from jarvis.tools.obsidian import obsidian_tools
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.vision import (
    VisionObserveScreenChangeTool,
    VisionExtractTextTool,
    VisionGetUITreeTool,
)
from jarvis.browser.playwright_backend import PlaywrightBackend
from jarvis.browser.service import BrowserService
from jarvis.tools.browser import browser_tools
from jarvis.tts.chatterbox import ChatterboxTTS
from jarvis.tts.piper import PiperTTS
from jarvis.tts.piper_setup import setup_piper_voice
from jarvis.tts.factory import build_text_to_speech
from jarvis.voice.pipeline import VoicePipeline
from jarvis.voice.state import VoicePipelineState
from jarvis.voice.barge_in import BargeInController
from jarvis.voice.audio_session import VoiceAudioSession
from jarvis.ui.control_center_model import ControlCenterBus
from jarvis.vision.composite_backend import CompositeVisionBackend
from jarvis.vision.execution_verifier import VisionToolExecutionVerifier
from jarvis.vision.service import VisionService
from jarvis.remote.gateway import RemoteGateway


def build_harness(
    config: JarvisConfig,
    resource_manager: ResourceManager,
    confirmation: ConfirmationProvider | None = None,
    recent_context: RecentContext | None = None,
    state_bus: AgentStateBus | None = None,
    desktop_kill_switch: DesktopKillSwitch | None = None,
    telemetry_bus: TelemetryBus | None = None,
    control_center_bus: ControlCenterBus | None = None,
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
            Capability.DESKTOP_READ: config.desktop_read_enabled,
            Capability.DESKTOP_CONTROL: config.desktop_control_enabled,
            Capability.VISION: config.vision_enabled,
            Capability.BROWSER: config.browser_enabled,
        }
    )
    audit_logger = AuditLogger(
        config.audit_log_path,
        telemetry_callback=(
            telemetry_bus.record_audit if telemetry_bus is not None else None
        ),
)
    vision_service = (
        VisionService(CompositeVisionBackend(), capability_policy)
        if capability_policy.is_enabled(Capability.VISION)
        else None
    )
    tool_executor = ToolExecutor(
        safety_manager=safety_manager,
        audit_logger=audit_logger,
        capability_policy=capability_policy,
        observer=(
            AgentToolExecutionObserver(state_bus)
            if state_bus is not None
            else None
        ),
        verifier=(
            VisionToolExecutionVerifier(vision_service)
            if vision_service is not None
            else None
        ),
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

    desktop_service = None
    if (
        capability_policy.is_enabled(Capability.DESKTOP_READ)
        or capability_policy.is_enabled(Capability.DESKTOP_CONTROL)
    ):
        desktop_service = build_desktop_service(
            desktop_kill_switch or DesktopKillSwitch()
        )
    if desktop_service is not None:
        for tool in desktop_tools(desktop_service):
            capability = (
                Capability.DESKTOP_CONTROL
                if tool.name in {
                    "desktop_focus_window",
                    "desktop_open_application",
                    "desktop_move_pointer_to_window",
                    "desktop_press_escape",
                    "desktop_close_window",
                }
                else Capability.DESKTOP_READ
            )
            if capability_policy.is_enabled(capability):
                tool_registry.register(tool)

    if vision_service is not None:
        tool_registry.register(
            VisionObserveScreenChangeTool(vision_service)
        )
        tool_registry.register(
            VisionExtractTextTool(vision_service)
        )
        tool_registry.register(
            VisionGetUITreeTool(vision_service)
        )

    browser_service = None
    if capability_policy.is_enabled(Capability.BROWSER):
        browser_service = BrowserService(
            PlaywrightBackend(),
            capability_policy=capability_policy,
            allowed_domains=config.browser_allowed_domains,
            blocked_domains=config.browser_blocked_domains,
            max_tabs=config.browser_max_tabs,
        )
    if browser_service is not None:
        for tool in browser_tools(browser_service):
            if capability_policy.is_enabled(Capability.BROWSER):
                tool_registry.register(tool)

    note_reference_resolver = None
    obsidian_vault = None
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

    skill_store = JsonSkillStore(config.skill_store_path)
    skill_mirror = None
    if (
        config.skills_obsidian_mirror_enabled
        and obsidian_vault is not None
        and capability_policy.is_enabled(Capability.OBSIDIAN_WRITE)
    ):
        skill_mirror = ObsidianSkillMirror(
            obsidian_vault, config.skills_obsidian_mirror_directory
        )

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
        skill_store=skill_store,
        skill_mirror=skill_mirror,
        telemetry_bus=telemetry_bus,
        control_center_bus=control_center_bus,
        vision_service=vision_service,
    )


def run_voice_mode(
    config: JarvisConfig,
    model_path: Path | None = None,
    microphone_device_index: int | None = None,
    state_callback: Callable[[VoicePipelineState], None] | None = None,
    wake_word_threshold: float | None = None,
    state_bus: AgentStateBus | None = None,
    desktop_kill_switch: DesktopKillSwitch | None = None,
    telemetry_bus: TelemetryBus | None = None,
) -> None:
    resource_manager = ResourceManager()
    resource_manager.idle()
    cancellation = CancellationController()
    recent_context = RecentContext()
    speech_context = SpeechContextBuilder(recent_context)

    detector = OpenWakeWordDetector(
        model_name=config.wake_word_model,
        model_path=model_path or config.wake_word_model_path,
        threshold=(
            config.wake_word_threshold
            if wake_word_threshold is None
            else wake_word_threshold
        ),
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
        config,
        resource_manager,
        confirmation,
        recent_context,
        state_bus,
        desktop_kill_switch,
        telemetry_bus,
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
        wake_acknowledgement_enabled=(
            config.voice_wake_acknowledgement_enabled
        ),
        wake_word_label=config.wake_word_phrase,
        debug_enabled=config.voice_debug_enabled,
        latency_debug_enabled=config.latency_debug_enabled,
        warm_up_stt_on_start=config.stt_warm_up_on_voice_start,
        warm_up_llm_on_start=config.llm_warm_up_on_voice_start,
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
        state_callback=state_callback,
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
        confirmation_state_callback = getattr(
            pipeline, "set_confirmation_state", None
        )
        if callable(confirmation_state_callback):
            confirmation.bind_state_callback(confirmation_state_callback)
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
        "--background",
        "--daemon",
        action="store_true",
        help="Executa em background sem console (requer --voice).",
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
    parser.add_argument(
        "--wake-word-threshold",
        type=float,
        help=(
            "Sensibilidade da wake word (0.0 a 1.0). Útil para calibrar "
            "a chamada curta 'Jarvis'."
        ),
    )
    parser.add_argument(
        "--tts-pace",
        choices=("fast", "natural", "calm"),
        help=(
            "Sobrescreve o ritmo do Piper apenas nesta execução; útil para "
            "calibrar a voz sem alterar data/settings.json."
        ),
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Abre a Ambient HUD conectada ao modo voice em outro processo.",
    )
    # Internal flag for background re-execution
    parser.add_argument(
        "--background-internal",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    # Windows service commands
    parser.add_argument(
        "--service-install",
        action="store_true",
        help="Instala o JARVIS como serviço do Windows (requer admin).",
    )
    parser.add_argument(
        "--service-uninstall",
        action="store_true",
        help="Remove o serviço do Windows (requer admin).",
    )
    parser.add_argument(
        "--service-start",
        action="store_true",
        help="Inicia o serviço do Windows.",
    )
    parser.add_argument(
        "--service-stop",
        action="store_true",
        help="Para o serviço do Windows.",
    )
    parser.add_argument(
        "--autostart-enable",
        action="store_true",
        help="Habilita inicialização automática no login do Windows.",
    )
    parser.add_argument(
        "--autostart-disable",
        action="store_true",
        help="Desabilita inicialização automática no login do Windows.",
    )
    # Remote Gateway
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Inicia o Remote Gateway; pode ser combinado com --voice.",
    )
    parser.add_argument(
        "--remote-host",
        type=str,
        default="127.0.0.1",
        help="Host para o Remote Gateway (padrão: 127.0.0.1). Use 0.0.0.0 para LAN (requer --remote-allow-lan).",
    )
    parser.add_argument(
        "--remote-port",
        type=int,
        default=8766,
        help="Porta para o Remote Gateway (padrão: 8766).",
    )
    parser.add_argument(
        "--remote-allow-lan",
        action="store_true",
        help="Permite conexões de rede local (LAN). ATENÇÃO: Exponha apenas em rede confiável ou com TLS/VPN.",
    )
    parser.add_argument(
        "--remote-cert",
        type=Path,
        help="Caminho para certificado TLS (.pem/.crt) para HTTPS/WSS.",
    )
    parser.add_argument(
        "--remote-key",
        type=Path,
        help="Caminho para chave privada TLS (.pem/.key) para HTTPS/WSS.",
    )
    parser.add_argument(
        "--remote-pair",
        action="store_true",
        help="Inicia pairing de novo dispositivo (exibe código temporário).",
    )
    parser.add_argument(
        "--remote-device-name",
        type=str,
        default="Remote Device",
        help="Nome vinculado ao código criado por --remote-pair.",
    )
    parser.add_argument(
        "--remote-devices",
        action="store_true",
        help="Lista dispositivos pareados.",
    )
    parser.add_argument(
        "--remote-revoke",
        type=str,
        metavar="DEVICE_ID",
        help="Revoga dispositivo pelo ID.",
    )
    return parser


def _service_name() -> str:
    return "JARVIS"


def _service_display_name() -> str:
    return "JARVIS Personal Assistant"


def _service_description() -> str:
    return "JARVIS - Agente pessoal local com voz, visão e automação"


def _get_service_executable() -> list[str]:
    """Retorna o comando para executar o serviço."""
    executable = sys.executable
    # Use pythonw.exe se disponível para rodar sem console
    if sys.platform == "win32":
        pythonw = Path(executable).with_name("pythonw.exe")
        if pythonw.exists():
            executable = str(pythonw)
    return [executable, "-m", "jarvis", "--voice", "--background"]


def _get_background_executable() -> list[str]:
    """Retorna o comando para executar em background (pythonw)."""
    executable = sys.executable
    if sys.platform == "win32":
        pythonw = Path(executable).with_name("pythonw.exe")
        if pythonw.exists():
            executable = str(pythonw)
    return [executable, "-m", "jarvis", "--voice", "--background-internal"]


def _acquire_single_instance_lock() -> tuple[bool, str | None]:
    """Tenta adquirir lock de instância única via arquivo. Retorna (sucesso, caminho_lock)."""
    import os
    from pathlib import Path

    lock_dir = Path("data/runtime")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "jarvis_background.lock"

    try:
        # Tenta criar o arquivo de lock exclusivamente
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        # Escreve o PID
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True, str(lock_file)
    except FileExistsError:
        # Verifica se o processo ainda está vivo
        try:
            with open(lock_file, 'r') as f:
                pid_str = f.read().strip()
                pid = int(pid_str)
            # Verifica se o processo existe
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                SYNCHRONIZE = 0x00100000
                handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                if handle:
                    # Processo existe
                    kernel32.CloseHandle(handle)
                    return False, None
                else:
                    # Processo morto, remove lock stale
                    os.remove(lock_file)
                    return _acquire_single_instance_lock()
            else:
                try:
                    os.kill(pid, 0)
                    return False, None
                except OSError:
                    # Processo morto
                    os.remove(lock_file)
                    return _acquire_single_instance_lock()
        except (ValueError, OSError):
            # Lock corrompido, remove e tenta novamente
            try:
                os.remove(lock_file)
            except OSError:
                pass
            return _acquire_single_instance_lock()
    except Exception:
        # Fail-open
        return True, None


def _release_single_instance_lock(lock_path: str | None) -> None:
    """Libera o lock de instância única."""
    if lock_path is None:
        return
    try:
        import os
        os.remove(lock_path)
    except OSError:
        pass


def _release_single_instance_mutex(mutex) -> None:
    """Libera o mutex de instância única."""
    import logging
    logger = logging.getLogger("jarvis.background.mutex")
    if mutex is not None:
        try:
            import win32api
            logger.info(f"Releasing mutex: {mutex}")
            win32api.CloseHandle(mutex)
        except Exception as e:
            logger.exception("Error releasing mutex: %s", e)


def _setup_background_logging() -> None:
    """Configura logging persistente para modo background."""
    import logging
    from pathlib import Path

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "jarvis.log"

    # Configura handler de arquivo
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)

    # Configura logger raiz
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Remove handlers existentes para evitar duplicação
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
    root_logger.addHandler(file_handler)

    # Também loga warnings e errors de bibliotecas
    logging.getLogger("jarvis").setLevel(logging.DEBUG)


def _run_background_mode(args, config: JarvisConfig) -> None:
    """Executa o JARVIS em modo background sem console."""
    if not args.voice and not config.wake_word_enabled:
        raise SystemExit("--background requer --voice ou wake_word_enabled no config.")

    # Configura logging persistente PRIMEIRO
    _setup_background_logging()
    import logging
    logger = logging.getLogger("jarvis.background")
    logger.info("Iniciando JARVIS background runtime (early log)")

    # Single-instance check
    acquired, lock_path = _acquire_single_instance_lock()
    if not acquired:
        logger.warning("JARVIS já está rodando em background.")
        print("JARVIS já está rodando em background.")
        return

    try:
        logger.info("Lock acquired, proceeding with startup")

        # Configura cancellation para sinais
        cancellation = CancellationController()
        _setup_signal_handlers(cancellation)

        # Registra cleanup no exit
        cleanup_functions = []

        def cleanup():
            logger.info("Encerrando JARVIS background runtime")
            for fn in reversed(cleanup_functions):
                try:
                    fn()
                except Exception as e:
                    logger.exception("Erro durante cleanup: %s", e)
            _release_single_instance_lock(lock_path)

        atexit.register(cleanup)

        desktop_kill_switch, desktop_kill_listener = _start_desktop_kill_switch(config)
        if desktop_kill_listener is not None:
            cleanup_functions.append(desktop_kill_listener.stop)

        state_server = None
        state_bus = None
        telemetry_bus = None

        # Sempre inicia IPC para permitir UI conectar
        state_bus = AgentStateBus()
        telemetry_bus = TelemetryBus()
        state_server = LocalStateServer(
            state_bus,
            endpoint_path=config.ambient_ui_endpoint_path,
            telemetry_bus=telemetry_bus,
        )
        state_server.start()
        cleanup_functions.append(state_server.close)

        state_callback = VoiceStateBridge(state_bus)

        try:
            voice_arguments = {
                "config": config,
                "model_path": args.wake_word_model_path,
                "microphone_device_index": args.microphone_device_index,
                "state_callback": state_callback,
                "wake_word_threshold": args.wake_word_threshold,
                "state_bus": state_bus,
                "telemetry_bus": telemetry_bus,
            }
            if config.desktop_control_enabled:
                voice_arguments["desktop_kill_switch"] = desktop_kill_switch

            run_voice_mode(**voice_arguments)
        except RuntimeError as error:
            logger.exception("Erro no modo voice: %s", error)
            raise SystemExit(f"Erro no modo voice: {error}") from error
        finally:
            cleanup()
    except Exception as e:
        import logging
        logger = logging.getLogger("jarvis.background")
        logger.exception("Erro fatal no background runtime: %s", e)
        _release_single_instance_lock(lock_path)
        raise


def _run_background_internal(args, config: JarvisConfig) -> None:
    """Entry point interno para execução em background (já desacoplado do console).
    O mutex já foi adquirido pelo processo pai."""
    # Remove --background-internal do args para evitar loop
    import sys
    sys.argv = [a for a in sys.argv if a not in ("--background-internal",)]
    # Re-parse args
    args = _argument_parser().parse_args(sys.argv[1:])
    _run_background_mode_already_holding_mutex(args, config)


def _run_background_mode_already_holding_mutex(args, config: JarvisConfig) -> None:
    """Executa o JARVIS em modo background - mutex já adquirido pelo pai."""
    if not args.voice and not config.wake_word_enabled:
        raise SystemExit("--background requer --voice ou wake_word_enabled no config.")

    # Configura logging persistente PRIMEIRO
    _setup_background_logging()
    import logging
    logger = logging.getLogger("jarvis.background")
    logger.info("Iniciando JARVIS background runtime (child process)")

    # Single-instance check no processo filho (o lock file pode ter sido deixado pelo pai)
    acquired, lock_path = _acquire_single_instance_lock()
    if not acquired:
        logger.warning("JARVIS já está rodando em background (detected in child).")
        print("JARVIS já está rodando em background.")
        return

    try:
        logger.info("Lock acquired in child, proceeding with startup")

        # Configura cancellation para sinais
        cancellation = CancellationController()
        _setup_signal_handlers(cancellation)

        # Registra cleanup no exit
        cleanup_functions = []

        def cleanup():
            logger.info("Encerrando JARVIS background runtime")
            for fn in reversed(cleanup_functions):
                try:
                    fn()
                except Exception as e:
                    logger.exception("Erro durante cleanup: %s", e)
            _release_single_instance_lock(lock_path)

        atexit.register(cleanup)

        desktop_kill_switch, desktop_kill_listener = _start_desktop_kill_switch(config)
        if desktop_kill_listener is not None:
            cleanup_functions.append(desktop_kill_listener.stop)

        state_server = None
        state_bus = None
        telemetry_bus = None

        # Sempre inicia IPC para permitir UI conectar
        state_bus = AgentStateBus()
        telemetry_bus = TelemetryBus()
        state_server = LocalStateServer(
            state_bus,
            endpoint_path=config.ambient_ui_endpoint_path,
            telemetry_bus=telemetry_bus,
        )
        state_server.start()
        cleanup_functions.append(state_server.close)

        state_callback = VoiceStateBridge(state_bus)

        try:
            voice_arguments = {
                "config": config,
                "model_path": args.wake_word_model_path,
                "microphone_device_index": args.microphone_device_index,
                "state_callback": state_callback,
                "wake_word_threshold": args.wake_word_threshold,
                "state_bus": state_bus,
                "telemetry_bus": telemetry_bus,
            }
            if config.desktop_control_enabled:
                voice_arguments["desktop_kill_switch"] = desktop_kill_switch

            run_voice_mode(**voice_arguments)
        except RuntimeError as error:
            logger.exception("Erro no modo voice: %s", error)
            raise SystemExit(f"Erro no modo voice: {error}") from error
        finally:
            cleanup()
    except Exception as e:
        import logging
        logger = logging.getLogger("jarvis.background")
        logger.exception("Erro fatal no background runtime: %s", e)
        raise


def _handle_service_command(args) -> int:
    """Processa comandos de serviço do Windows."""
    if sys.platform != "win32":
        print("Comandos de serviço disponíveis apenas no Windows.")
        return 1

    try:
        import win32serviceutil
        import win32service
        import win32event
        import servicemanager
    except ImportError:
        print("pywin32 não instalado. Instale com: pip install pywin32")
        return 1

    if args.service_install:
        return _install_service()
    elif args.service_uninstall:
        return _uninstall_service()
    elif args.service_start:
        return _start_service()
    elif args.service_stop:
        return _stop_service()
    return 0


def _install_service() -> int:
    """Instala o JARVIS como serviço do Windows."""
    import win32serviceutil
    import win32service

    try:
        cmd = _get_service_executable()
        win32serviceutil.InstallService(
            " ".join(f'"{c}"' if " " in c else c for c in cmd),
            _service_name(),
            _service_display_name(),
            description=_service_description(),
            startType=win32service.SERVICE_AUTO_START,
        )
        print(f"Serviço '{_service_display_name()}' instalado com sucesso.")
        print("Use 'jarvis --service-start' para iniciar.")
        return 0
    except Exception as e:
        print(f"Erro ao instalar serviço: {e}")
        return 1


def _uninstall_service() -> int:
    """Remove o serviço do Windows."""
    import win32serviceutil

    try:
        win32serviceutil.RemoveService(_service_name())
        print(f"Serviço '{_service_display_name()}' removido com sucesso.")
        return 0
    except Exception as e:
        print(f"Erro ao remover serviço: {e}")
        return 1


def _start_service() -> int:
    """Inicia o serviço do Windows."""
    import win32serviceutil

    try:
        win32serviceutil.StartService(_service_name())
        print(f"Serviço '{_service_display_name()}' iniciado.")
        return 0
    except Exception as e:
        print(f"Erro ao iniciar serviço: {e}")
        return 1


def _stop_service() -> int:
    """Para o serviço do Windows."""
    import win32serviceutil

    try:
        win32serviceutil.StopService(_service_name())
        print(f"Serviço '{_service_display_name()}' parado.")
        return 0
    except Exception as e:
        print(f"Erro ao parar serviço: {e}")
        return 1


def _handle_autostart_command(args) -> int:
    """Processa comandos de auto-inicialização."""
    if sys.platform != "win32":
        print("Auto-inicialização disponível apenas no Windows.")
        return 1

    try:
        import winreg
    except ImportError:
        print("winreg não disponível.")
        return 1

    if args.autostart_enable:
        return _enable_autostart()
    elif args.autostart_disable:
        return _disable_autostart()
    return 0


def _enable_autostart() -> int:
    """Habilita inicialização automática via Registro do Windows."""
    import winreg

    try:
        cmd = _get_service_executable()
        cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd)

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "JARVIS", 0, winreg.REG_SZ, cmd_str)
        winreg.CloseKey(key)
        print("Auto-inicialização habilitada.")
        return 0
    except Exception as e:
        print(f"Erro ao habilitar auto-inicialização: {e}")
        return 1


def _disable_autostart() -> int:
    """Desabilita inicialização automática via Registro do Windows."""
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, "JARVIS")
        winreg.CloseKey(key)
        print("Auto-inicialização desabilitada.")
        return 0
    except FileNotFoundError:
        print("Auto-inicialização não estava habilitada.")
        return 0
    except Exception as e:
        print(f"Erro ao desabilitar auto-inicialização: {e}")
        return 1


def _setup_signal_handlers(cancellation: CancellationController) -> None:
    """Configura handlers de sinal para encerramento gracioso."""
    def signal_handler(signum, frame):
        cancellation.cancel()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, signal_handler)


def _run_background_mode(args, config: JarvisConfig) -> None:
    """Executa o JARVIS em modo background sem console."""
    if not args.voice and not config.wake_word_enabled:
        raise SystemExit("--background requer --voice ou wake_word_enabled no config.")

    # Configura cancellation para sinais
    cancellation = CancellationController()
    _setup_signal_handlers(cancellation)

    # Registra cleanup no exit
    cleanup_functions = []

    def cleanup():
        for fn in reversed(cleanup_functions):
            try:
                fn()
            except Exception:
                pass

    atexit.register(cleanup)

    desktop_kill_switch, desktop_kill_listener = _start_desktop_kill_switch(config)
    if desktop_kill_listener is not None:
        cleanup_functions.append(desktop_kill_listener.stop)

    state_server = None
    state_bus = None
    telemetry_bus = None

    # Sempre inicia IPC para permitir UI conectar
    state_bus = AgentStateBus()
    telemetry_bus = TelemetryBus()
    state_server = LocalStateServer(
        state_bus,
        endpoint_path=config.ambient_ui_endpoint_path,
        telemetry_bus=telemetry_bus,
    )
    state_server.start()
    cleanup_functions.append(state_server.close)

    state_callback = VoiceStateBridge(state_bus)

    try:
        voice_arguments = {
            "config": config,
            "model_path": args.wake_word_model_path,
            "microphone_device_index": args.microphone_device_index,
            "state_callback": state_callback,
            "wake_word_threshold": args.wake_word_threshold,
            "state_bus": state_bus,
            "telemetry_bus": telemetry_bus,
        }
        if config.desktop_control_enabled:
            voice_arguments["desktop_kill_switch"] = desktop_kill_switch

        run_voice_mode(**voice_arguments)
    except RuntimeError as error:
        raise SystemExit(f"Erro no modo voice: {error}") from error
    finally:
        cleanup()


def _run_as_service() -> None:
    """Entry point para execução como serviço do Windows."""
    if sys.platform != "win32":
        return

    try:
        import win32serviceutil
        import win32service
        import win32event
        import servicemanager
    except ImportError:
        return

    class JarvisService(win32serviceutil.ServiceFramework):
        _svc_name_ = _service_name()
        _svc_display_name_ = _service_display_name()
        _svc_description_ = _service_description()

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self.cancellation = CancellationController()

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.cancellation.cancel()
            win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            self.main()

        def main(self):
            config = JarvisConfig()
            # Configura wake word se habilitado
            if not config.wake_word_enabled:
                return

            desktop_kill_switch, desktop_kill_listener = _start_desktop_kill_switch(config)

            state_bus = AgentStateBus()
            telemetry_bus = TelemetryBus()
            state_server = LocalStateServer(
                state_bus,
                endpoint_path=config.ambient_ui_endpoint_path,
                telemetry_bus=telemetry_bus,
            )
            state_server.start()

            state_callback = VoiceStateBridge(state_bus)

            try:
                voice_arguments = {
                    "config": config,
                    "model_path": None,
                    "microphone_device_index": config.microphone_device_index,
                    "state_callback": state_callback,
                    "wake_word_threshold": None,
                    "state_bus": state_bus,
                    "telemetry_bus": telemetry_bus,
                }
                if config.desktop_control_enabled:
                    voice_arguments["desktop_kill_switch"] = desktop_kill_switch

                run_voice_mode(**voice_arguments)
            finally:
                state_server.close()
                if desktop_kill_listener is not None:
                    desktop_kill_listener.stop()

    win32serviceutil.HandleCommandLine(JarvisService)


def _start_desktop_kill_switch(
    config: JarvisConfig,
) -> tuple[DesktopKillSwitch, WindowsDesktopKillSwitchListener | None]:
    kill_switch = DesktopKillSwitch()
    if not config.desktop_control_enabled:
        return kill_switch, None

    listener = WindowsDesktopKillSwitchListener(kill_switch)
    if listener.start():
        print("Desktop control ativo. Kill switch: Ctrl+Alt+K.")
    else:
        print(
            "Desktop control bloqueado: não foi possível registrar "
            "o kill switch Ctrl+Alt+K."
        )
    return kill_switch, listener


def main(argv: list[str] | None = None) -> None:
    args = _argument_parser().parse_args(argv)
    config = JarvisConfig()
    if args.tts_pace is not None:
        config.tts_piper_pace = args.tts_pace

    # Handle service commands first (Windows only)
    if any([args.service_install, args.service_uninstall, args.service_start, args.service_stop]):
        sys.exit(_handle_service_command(args))

    # Handle autostart commands
    if any([args.autostart_enable, args.autostart_disable]):
        sys.exit(_handle_autostart_command(args))

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

    # Internal background mode (already detached via pythonw)
    if getattr(args, "background_internal", False):
        _run_background_internal(args, config)
        return

    # Background mode (requires --voice)
    if args.background:
        if sys.platform == "win32":
            # No Windows, re-executa com pythonw.exe para desacoplar do console
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            if pythonw.exists():
                # Single-instance check NO PAI antes de lançar o filho
                acquired, lock_path = _acquire_single_instance_lock()
                if not acquired:
                    print("JARVIS já está rodando em background.")
                    return
                # Passa --background-internal para evitar loop infinito
                cmd = [str(pythonw), "-m", "jarvis"]
                for arg in sys.argv[1:]:
                    if arg in ("--background", "--daemon"):
                        cmd.append("--background-internal")
                    else:
                        cmd.append(arg)
                import subprocess
                # DETACHED_PROCESS para não herdar console
                subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
                print("JARVIS iniciado em background.")
                return
        # Fallback ou não-Windows: executa internamente
        _run_background_mode(args, config)
        return

    # Remote Gateway commands (pair, devices, revoke)
    if any([args.remote_pair, args.remote_devices, args.remote_revoke]):
        _handle_remote_command(args, config)
        return

    # Remote Gateway mode (independent, can combine with --voice)
    if args.remote:
        _run_remote_gateway(args, config)
        return

    if args.voice or args.ui or config.wake_word_enabled:
        if (
            args.wake_word_threshold is not None
            and not 0.0 <= args.wake_word_threshold <= 1.0
        ):
            raise SystemExit(
                "Erro no modo voice: --wake-word-threshold deve estar "
                "entre 0.0 e 1.0."
            )

        desktop_kill_switch, desktop_kill_listener = (
            _start_desktop_kill_switch(config)
        )
        state_server = None
        ui_process = None
        state_callback = None
        state_bus = None
        telemetry_bus = None
        if args.ui:
            state_bus = AgentStateBus()
            telemetry_bus = TelemetryBus()
            state_server = LocalStateServer(
                state_bus,
                endpoint_path=config.ambient_ui_endpoint_path,
                telemetry_bus=telemetry_bus,
            )
            state_server.start()
            state_callback = VoiceStateBridge(state_bus)
            ui_process = launch_ambient_ui(config.ambient_ui_endpoint_path)

        try:
            voice_arguments = {
                "config": config,
                "model_path": args.wake_word_model_path,
                "microphone_device_index": args.microphone_device_index,
                "state_callback": state_callback,
                "wake_word_threshold": args.wake_word_threshold,
            }
            if state_bus is not None:
                voice_arguments["state_bus"] = state_bus
            if telemetry_bus is not None:
                voice_arguments["telemetry_bus"] = telemetry_bus
            if config.desktop_control_enabled:
                voice_arguments["desktop_kill_switch"] = desktop_kill_switch
            run_voice_mode(**voice_arguments)
        except RuntimeError as error:
            raise SystemExit(f"Erro no modo voice: {error}") from error
        finally:
            stop_spawned_ambient_ui(ui_process)
            if state_server is not None:
                state_server.close()
            if desktop_kill_listener is not None:
                desktop_kill_listener.stop()
        return

    desktop_kill_switch, desktop_kill_listener = (
        _start_desktop_kill_switch(config)
    )
    resource_manager = ResourceManager()
    harness = build_harness(
        config,
        resource_manager,
        desktop_kill_switch=desktop_kill_switch,
    )
    try:
        harness.run()
    finally:
        if desktop_kill_listener is not None:
            desktop_kill_listener.stop()


def _handle_remote_command(args, config: JarvisConfig) -> None:
    """Processa comandos de gerenciamento do Remote Gateway."""
    import time
    from jarvis.remote.auth import DeviceRegistry, PairingManager
    from jarvis.security.audit import AuditLogger

    audit_logger = AuditLogger(config.audit_log_path)
    registry = DeviceRegistry(audit=audit_logger)

    if args.remote_pair:
        pairing_manager = PairingManager(audit=audit_logger)
        pending = pairing_manager.create_pairing(args.remote_device_name)
        print(f"\nCódigo de pairing: {pending.pairing_code}")
        print(
            "Expira em: "
            f"{max(0, int((pending.expires_at - time.time()) / 60))} minutos"
        )
        print("O código foi criado localmente e só pode ser usado uma vez.")
        return

    if args.remote_devices:
        devices = registry.list_devices()
        if not devices:
            print("Nenhum dispositivo pareado.")
            return
        print("\nDispositivos pareados:")
        for device in devices:
            status = "REVOGADO" if device.revoked else "ATIVO"
            last_seen = device.last_seen[:19].replace("T", " ")
            print(
                f"  {device.id} | {device.name} | {status} | "
                f"Último: {last_seen}"
            )
        return

    if args.remote_revoke:
        if registry.revoke(args.remote_revoke):
            print(f"Dispositivo {args.remote_revoke} revogado.")
        else:
            print(f"Dispositivo {args.remote_revoke} não encontrado.")


def _run_remote_gateway(args, config: JarvisConfig) -> None:
    """Executa o Remote Gateway, opcionalmente junto com o runtime voice."""
    import time

    from jarvis.application.state import AgentStateBus
    from jarvis.application.telemetry import TelemetryBus
    from jarvis.security.audit import AuditLogger
    from jarvis.remote.core_port import HarnessRemoteCorePort

    # Aviso se expor LAN
    if args.remote_allow_lan and args.remote_host != "127.0.0.1":
        print("\n⚠️  ATENÇÃO: Remote Gateway exposto na LAN!")
        print("   Use apenas em rede confiável ou com TLS/VPN.")
        print("   Para produção, configure certificados TLS (--remote-cert/--remote-key).\n")

    # Configura cancellation para sinais
    cancellation = CancellationController()
    _setup_signal_handlers(cancellation)

    # Componentes compartilhados
    state_bus = AgentStateBus()
    telemetry_bus = TelemetryBus()
    audit_logger = AuditLogger(config.audit_log_path)

    # Desktop kill switch
    desktop_kill_switch, desktop_kill_listener = _start_desktop_kill_switch(config)

    # Harness para processar mensagens
    harness = build_harness(
        config,
        ResourceManager(),
        confirmation=None,
        state_bus=state_bus,
        telemetry_bus=telemetry_bus,
        desktop_kill_switch=desktop_kill_switch,
    )

    # Remote Gateway
    gateway = RemoteGateway(
        core_port=HarnessRemoteCorePort(harness),
        state_bus=state_bus,
        telemetry_bus=telemetry_bus,
        audit=audit_logger,
        host=args.remote_host,
        port=args.remote_port,
        certfile=str(args.remote_cert) if args.remote_cert else None,
        keyfile=str(args.remote_key) if args.remote_key else None,
        allow_lan=args.remote_allow_lan,
    )

    # Voice mode (opcional)
    voice_context = None
    if args.voice:
        state_callback = VoiceStateBridge(state_bus)
        voice_arguments = {
            "config": config,
            "model_path": args.wake_word_model_path,
            "microphone_device_index": args.microphone_device_index,
            "state_callback": state_callback,
            "wake_word_threshold": args.wake_word_threshold,
            "state_bus": state_bus,
            "telemetry_bus": telemetry_bus,
        }
        if config.desktop_control_enabled:
            voice_arguments["desktop_kill_switch"] = desktop_kill_switch
        voice_context = voice_arguments

    print(f"Remote Gateway iniciado em {args.remote_host}:{args.remote_port}")
    if args.remote_allow_lan:
        print("Modo LAN habilitado - conexões de rede local permitidas")
    if args.remote_cert and args.remote_key:
        print("TLS habilitado")
    if voice_context:
        print("Modo voice habilitado")

    try:
        gateway.start()
        # Inicia voice mode se configurado
        if voice_context:
            voice_thread = threading.Thread(
                target=run_voice_mode,
                kwargs=voice_context,
                name="jarvis-voice-mode",
                daemon=True,
            )
            voice_thread.start()
        # Mantém vivo até sinal
        while not cancellation.is_cancelled:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        gateway.stop()
        if desktop_kill_listener is not None:
            desktop_kill_listener.stop()
        harness.close()


if __name__ == "__main__":
    main()
