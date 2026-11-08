import argparse
import atexit
import os
import signal
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from jarvis.activation.listener import WakeWordListener
from jarvis.activation.openwakeword import OpenWakeWordDetector
from jarvis.application.audio_activity import AudioActivityBus
from jarvis.application.conversation_log import ConversationLogBus
from jarvis.application.state import AgentStateBus
from jarvis.application.telemetry import TelemetryBus
from jarvis.application.state_ipc import LocalStateServer
from jarvis.application.voice_bridge import VoiceStateBridge
from jarvis.application.tool_execution_state import AgentToolExecutionObserver
from jarvis.audio.input import PvRecorderAudioInput
from jarvis.audio.devices import resolve_microphone_index
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
from jarvis.speech.gpu_budget import GpuBudgetManager, query_gpu_memory
from jarvis.speech.profile import parse_stt_profile
from jarvis.speech.stt_process import SttModelConfig, SttProcessClient
from jarvis.speech.metrics import SpeechRecognitionMetricsRecorder
from jarvis.speech.ptbr_fallback import PtBrFallbackClient
from jarvis.speech.normalizer import TranscriptNormalizer
from jarvis.speech.validator import TranscriptionValidator
from jarvis.speech.context import SpeechContextBuilder
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.delete_file import DeleteFileTool
from jarvis.tools.desktop import desktop_tools
from jarvis.tools.local_control import local_control_tools
from jarvis.tools.organization import OrganizationExecuteTool, OrganizationUndoTool
from jarvis.tools.application_catalog import application_catalog_tools
from jarvis.local_control.service import LocalControlService
from jarvis.local_control.windows import build_windows_local_control_backend
from jarvis.media.service import GsmtcMediaService
from jarvis.media.winrt_adapter import WinrtGsmtcAdapter
from jarvis.tools.media import media_tools
from jarvis.safe_browser.policy import UrlPolicy
from jarvis.safe_browser.service import SafeBrowserService
from jarvis.safe_browser.windows import WindowsDefaultBrowserLauncher
from jarvis.tools.safe_browser import safe_browser_tools
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
from jarvis.voice.diagnostic import VoiceDiagnosticRecorder
from jarvis.voice.state import VoicePipelineState
from jarvis.voice.barge_in import BargeInController
from jarvis.voice.audio_session import VoiceAudioSession
from jarvis.ui.control_center_model import ControlCenterBus
from jarvis.vision.composite_backend import CompositeVisionBackend
from jarvis.vision.execution_verifier import VisionToolExecutionVerifier
from jarvis.vision.service import VisionService
from jarvis.remote.gateway import RemoteGateway
from jarvis.integrations.google.calendar.limited_write import (
    LimitedCalendarCreateTool, LimitedCalendarUpdateTool,
)
from jarvis.integrations.google.calendar.client import GoogleCalendarApiClient
from jarvis.integrations.google.calendar.google_provider import GoogleCalendarProvider, GoogleCalendarWriteProvider
from jarvis.integrations.google.calendar.tools import (
    GoogleCalendarListEventsTool,
    GoogleCalendarGetEventTool,
    GoogleCalendarCheckAvailabilityTool,
    GoogleCalendarCreateEventTool,
    GoogleCalendarUpdateEventTool,
)
from jarvis.integrations.google.factory import build_google_auth_manager_with_calendar, build_google_calendar_limited_write_auth
from jarvis.integrations.google.factory import build_google_calendar_minimal_read_auth
from jarvis.integrations.google.factory import build_google_drive_docs_auth, drive_docs_grant_binding
from jarvis.integrations.google.drive_docs.client import DriveDocsClient
from jarvis.integrations.google.drive_docs.service import DriveDocsService
from jarvis.integrations.google.drive_docs.tools import (
    DriveFindFilesTool, DriveSelectFileTool, DriveMetadataTool, DocsReadTool,
)
from jarvis.integrations.google.calendar.read_tool import GoogleCalendarReadAgendaTool


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
        context_window=config.ollama_context_window,
    )
    safety_manager = SafetyManager(confirmation=confirmation)
    from jarvis.security.permissions import PermissionStore
    from jarvis.memory.managed import ManagedMemoryStore
    roots = [config.filesystem_base_directory]
    if config.obsidian_vault_path is not None:
        roots.append(config.obsidian_vault_path)
    permissions = PermissionStore(config.settings_path.parent / "permissions.json", tuple(roots))
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
            Capability.GOOGLE_CALENDAR_READ: config.google_calendar_enabled,
            Capability.GOOGLE_CALENDAR_WRITE: config.google_calendar_write_enabled,
            Capability.GOOGLE_DRIVE_READ: config.google_drive_read_enabled,
            Capability.GOOGLE_DOCS_READ: config.google_docs_read_enabled,
            Capability.LOCAL_CONTROL: config.local_control_enabled,
            Capability.MEDIA_CONTROL: config.gsmtc_enabled,
            Capability.SAFE_BROWSER: config.safe_browser_enabled,
            Capability.LOCAL_BRIEFING_READ: config.briefing_enabled,
        }, administrative=permissions
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
        permissions=permissions,
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
        base_directory=config.filesystem_base_directory,
        permissions=permissions,
    )
    memory = ConversationMemory(
        max_messages=config.conversation_max_messages
    )
    memory_store = ManagedMemoryStore(SQLiteMemoryStore(config.database_path), permissions)
    recent_context = recent_context or RecentContext()
    file_reference_resolver = FileReferenceResolver(
        filesystem,
        recent_context,
    )

    tool_registry = ToolRegistry()
    organization = None
    if sys.platform == 'win32':
        from jarvis.organization.service import OrganizationService
        organization = OrganizationService(config.settings_path.parent / 'organization', permissions)
        if capability_policy.is_enabled(Capability.FILESYSTEM_WRITE):
            tool_registry.register(OrganizationExecuteTool(organization))
            tool_registry.register(OrganizationUndoTool(organization))
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

    application_control = None
    if os.name == "nt":
        from jarvis.desktop.application_catalog import ApplicationCatalog
        from jarvis.desktop.application_discovery import WindowsApplicationDiscovery
        from jarvis.desktop.application_windows import WindowsApplicationBackend
        from jarvis.desktop.application_control import ApplicationControlService, ApplicationSettings
        application_control = ApplicationControlService(ApplicationCatalog(WindowsApplicationDiscovery()),
            WindowsApplicationBackend(), permissions, ApplicationSettings(config.settings_path.parent / 'applications.json'),
            desktop_kill_switch, enabled=config.local_control_enabled and config.application_catalog_enabled)
        if application_control.enabled:
            permissions.application_control = application_control
            for tool in application_catalog_tools(application_control):
                tool_registry.register(tool)
    if config.local_control_enabled:
        local_control_service = LocalControlService(
            build_windows_local_control_backend(),
            application_control=application_control if application_control and application_control.enabled else None,
        )
        for tool in local_control_tools(local_control_service):
            tool_registry.register(tool)
    if config.gsmtc_enabled:
        media_service = GsmtcMediaService(WinrtGsmtcAdapter())
        for tool in media_tools(media_service):
            tool_registry.register(tool)
    if config.safe_browser_enabled:
        url_policy = UrlPolicy()
        safe_browser_service = SafeBrowserService(
            WindowsDefaultBrowserLauncher(url_policy), url_policy,
        )
        for tool in safe_browser_tools(safe_browser_service):
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

    from jarvis.integrations.google.accounts import AccountService, LegacyBoundAccountAuth
    accounts = AccountService(config)
    permissions.protected_paths.add(accounts.directory.resolve())

    def account_auth(operation, legacy_builder):
        if accounts.path.exists():
            return accounts.auth(operation)
        return LegacyBoundAccountAuth(accounts, operation, legacy_builder())

    calendar_service = None
    if capability_policy.is_enabled(Capability.GOOGLE_DRIVE_READ):
        from jarvis.integrations.google.drive_docs.picker import LocalDocumentAuthorizations
        drive_auth = account_auth('drive_docs_read', build_google_drive_docs_auth)
        def current_drive_binding():
            drive_auth.binding()
            from jarvis.integrations.google.accounts import grant_digest
            material = accounts._material(drive_auth.snapshot)
            return grant_digest(material)

        drive_docs = DriveDocsService(
            DriveDocsClient(drive_auth, current_drive_binding),
            config.google_drive_docs_files, config.google_drive_docs_folders,
            config.google_drive_docs_grant,
            policy_current=LocalDocumentAuthorizations(config).watch(),
        )
        tool_registry.register(DriveFindFilesTool(drive_docs))
        tool_registry.register(DriveSelectFileTool(drive_docs))
        tool_registry.register(DriveMetadataTool(drive_docs))
        if capability_policy.is_enabled(Capability.GOOGLE_DOCS_READ):
            tool_registry.register(DocsReadTool(drive_docs))
    if capability_policy.is_enabled(Capability.GOOGLE_CALENDAR_READ):
        auth_manager = account_auth('calendar_read', build_google_calendar_minimal_read_auth)
        timezone = datetime.now().astimezone().tzinfo
        calendar_service = GoogleCalendarProvider(
            GoogleCalendarApiClient(auth_manager), max_event_pages=5
        )
    if calendar_service is not None:
        tool_registry.register(
            GoogleCalendarReadAgendaTool(
                calendar_service, timezone, (auth_manager.snapshot['resources'] if auth_manager.snapshot is not None
                                              else config.google_calendar_authorized_calendars)
            )
        )
        # Legacy detailed/freeBusy tools require a broader grant and are not
        # registered by the MVP read flag. Their implementations remain intact.

    calendar_write_service = None
    if capability_policy.is_enabled(Capability.GOOGLE_CALENDAR_WRITE):
        auth_manager = account_auth('calendar_write', build_google_calendar_limited_write_auth)
        write_auth = auth_manager
        timezone = datetime.now().astimezone().tzinfo
        calendar_write_service = GoogleCalendarWriteProvider(
            GoogleCalendarApiClient(auth_manager)
        )
    def calendar_write_policy():
        current = JarvisConfig(settings_path=config.settings_path)
        if not current.google_calendar_write_enabled:
            return {}
        write_auth.binding()
        return write_auth.snapshot['resources']

    if calendar_write_service is not None:
        from jarvis.integrations.google.calendar.operation_journal import CalendarOperationJournal
        write_journal = CalendarOperationJournal(config.settings_path.parent / "calendar-write-operations.json")
        tool_registry.register(
            LimitedCalendarCreateTool(calendar_write_service,
                                      write_auth.snapshot['resources'] if write_auth.snapshot is not None else config.google_calendar_write_calendars,
                                      write_auth.binding, policy_current=calendar_write_policy,
                                      journal=write_journal, account_label=lambda: write_auth.snapshot['label'] +
                                      ' (' + write_auth.snapshot['namespace'] + ':' + write_auth.snapshot['subject'] + ')')
        )
        tool_registry.register(
            LimitedCalendarUpdateTool(calendar_write_service,
                                      write_auth.snapshot['resources'] if write_auth.snapshot is not None else config.google_calendar_write_calendars,
                                      write_auth.binding, policy_current=calendar_write_policy,
                                      journal=write_journal, account_label=lambda: write_auth.snapshot['label'] +
                                      ' (' + write_auth.snapshot['namespace'] + ':' + write_auth.snapshot['subject'] + ')')
        )

    note_reference_resolver = None
    obsidian_vault = None
    if (
        config.obsidian_enabled
        and config.obsidian_vault_path is not None
        and config.obsidian_vault_path.is_dir()
    ):
        obsidian_filesystem = FileSystemGuard(
            base_directory=config.obsidian_vault_path,
            permissions=permissions,
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
        application_control=application_control,
        organization=organization,
        accounts=accounts,
    )


def build_voice_pipeline(
    config: JarvisConfig,
    model_path: Path | None = None,
    microphone_device_index: int | None = None,
    state_callback: Callable[[VoicePipelineState], None] | None = None,
    wake_word_threshold: float | None = None,
    state_bus: AgentStateBus | None = None,
    desktop_kill_switch: DesktopKillSwitch | None = None,
    telemetry_bus: TelemetryBus | None = None,
    audio_activity_bus: AudioActivityBus | None = None,
    conversation_log_bus: ConversationLogBus | None = None,
    resource_manager: ResourceManager | None = None,
    harness_builder: Callable[
        [ConfirmationProvider | None, RecentContext], Harness
    ] | None = None,
    gpu_budget: GpuBudgetManager | None = None,
) -> VoicePipeline:
    diagnostic_path = os.getenv("JARVIS_VOICE_DIAGNOSTIC_PATH")
    wav_directory = os.getenv("JARVIS_VOICE_DIAGNOSTIC_WAV_DIR")
    wav_limit = int(os.getenv("JARVIS_VOICE_DIAGNOSTIC_WAV_LIMIT", "10"))
    diagnostic = (
        VoiceDiagnosticRecorder(
            Path(diagnostic_path),
            wav_directory=Path(wav_directory) if wav_directory else None,
            wav_limit=wav_limit if wav_directory else 0,
        )
        if diagnostic_path else None
    )
    resource_manager = resource_manager or ResourceManager()
    resource_manager.idle()
    cancellation = CancellationController()
    recent_context = RecentContext()
    speech_context = SpeechContextBuilder(recent_context)

    wake_threshold = (
        config.wake_word_threshold
        if wake_word_threshold is None
        else wake_word_threshold
    )
    verifier_path = (
        config.wake_word_verifier_path
        if (
            config.wake_word_verifier_enabled
            and config.wake_word_verifier_path.is_file()
        )
        else None
    )
    detector = OpenWakeWordDetector(
        model_name=config.wake_word_model,
        model_path=model_path or config.wake_word_model_path,
        threshold=wake_threshold,
        verifier_path=verifier_path,
        verifier_candidate_threshold=(
            config.wake_word_verifier_candidate_threshold
        ),
        verifier_threshold=(
            config.wake_word_verifier_threshold
            if wake_word_threshold is None
            else wake_word_threshold
        ),
        verifier_strong_threshold=max(
            config.wake_word_verifier_strong_threshold,
            (
                config.wake_word_verifier_threshold
                if wake_word_threshold is None
                else wake_word_threshold
            ),
        ),
        minimum_consecutive_detections=(
            config.wake_min_consecutive_detections
        ),
    )
    try:
        resolved_mic_index = resolve_microphone_index(
            config.microphone_device_name,
            (
                microphone_device_index
                if microphone_device_index is not None
                else config.microphone_device_index
            ),
            warning_callback=print,
        )
        from jarvis.security.permissions import PermissionStore
        audio_permissions = PermissionStore(config.settings_path.parent / "permissions.json",
            tuple(p for p in (config.filesystem_base_directory, config.obsidian_vault_path) if p is not None))
        audio_input = PvRecorderAudioInput(
            frame_length=detector.frame_length,
            device_index=resolved_mic_index,
            permission_check=lambda: audio_permissions.require("microphone.capture"),
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
    audio_level_callback = (
        audio_activity_bus.publish
        if audio_activity_bus is not None
        else None
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
        noise_calibration_seconds=config.voice_noise_calibration_seconds,
        noise_margin=config.voice_noise_margin,
        maximum_speech_threshold=config.voice_maximum_speech_threshold,
        pre_roll_seconds=config.voice_pre_roll_seconds,
        end_silence_seconds=config.voice_end_silence_seconds,
        level_callback=audio_level_callback,
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

    active_stt_profile = parse_stt_profile(config.stt_profile)
    gpu_budget = gpu_budget or GpuBudgetManager(query_gpu_memory)

    def stt_preload_guard() -> None:
        if active_stt_profile.minimum_free_vram_mib:
            gpu_budget.require(active_stt_profile.minimum_free_vram_mib)

    if config.stt_execution_mode == "process":
        stt_preload_guard()
        speech_to_text = SttProcessClient(
            SttModelConfig(
                model_name=active_stt_profile.model,
                device=active_stt_profile.device,
                compute_type=active_stt_profile.compute_type,
                language=config.stt_language,
                vad_enabled=config.stt_vad_enabled,
                vad_min_silence_duration_ms=config.stt_vad_min_silence_duration_ms,
                vad_speech_pad_ms=config.stt_vad_speech_pad_ms,
                vad_threshold=config.stt_vad_threshold,
                initial_prompt=config.stt_initial_prompt,
                hotwords=(
                    speech_context.build_hotwords()
                    if config.stt_hotwords_enabled else None
                ),
            ),
            startup_timeout_seconds=config.stt_preload_timeout_seconds,
        )
    else:
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
            allow_device_fallback=active_stt_profile.allow_device_fallback,
            preload_guard=stt_preload_guard,
        )
    transcription_fallback = (
        PtBrFallbackClient(
            model_name=config.stt_ptbr_fallback_model,
            minimum_confidence=config.stt_ptbr_fallback_min_confidence,
            minimum_margin=config.stt_ptbr_fallback_min_margin,
            startup_timeout_seconds=(
                config.stt_ptbr_fallback_startup_timeout_seconds
            ),
            response_timeout_seconds=(
                config.stt_ptbr_fallback_response_timeout_seconds
            ),
        )
        if config.stt_ptbr_fallback_enabled
        else None
    )
    text_to_speech = build_text_to_speech(
        config,
        activity_callback=audio_level_callback,
    )
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
            level_callback=audio_level_callback,
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
                0.0
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
            level_callback=audio_level_callback,
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
            level_callback=audio_level_callback,
        )

    harness = (
        harness_builder(confirmation, recent_context)
        if harness_builder is not None
        else build_harness(
            config,
            resource_manager,
            confirmation,
            recent_context,
            state_bus,
            desktop_kill_switch,
            telemetry_bus,
        )
    )
    candidate_shadow = None
    if (
        os.getenv("JARVIS_STT_CANDIDATE_SHADOW", "").strip().casefold()
        in {"1", "true", "yes", "on", "sim"}
    ):
        if active_stt_profile.name == "large-v3":
            print(
                "[stt-shadow] disabled reason=incompatible_primary "
                "primary=large-v3"
            )
    pipeline = VoicePipeline(
        listener=listener,
        recorder=recorder,
        speech_to_text=speech_to_text,
        harness=harness,
        diagnostic=diagnostic,
        semantic_shadow_enabled=(
            os.getenv("JARVIS_SEMANTIC_SHADOW", "").strip().casefold()
            in {"1", "true", "yes", "on", "sim"}
        ),
        stt_candidate_shadow=candidate_shadow,
        resource_manager=resource_manager,
        text_to_speech=text_to_speech,
        tts_enabled=config.tts_enabled,
        tts_enabled_game_mode=config.tts_enabled_game_mode,
        post_speech_cooldown_seconds=(
            config.voice_post_tts_grace_seconds
        ),
        sample_rate=detector.sample_rate,
        transcription_validator=TranscriptionValidator(),
        transcription_metrics_recorder=(
            SpeechRecognitionMetricsRecorder(
                config.voice_recognition_metrics_path
            )
            if config.voice_recognition_metrics_enabled
            else None
        ),
        transcription_fallback=transcription_fallback,
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
        stt_preload_timeout_seconds=config.stt_preload_timeout_seconds,
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
        conversation_log_bus=conversation_log_bus,
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
    return pipeline


def run_voice_mode(
    config: JarvisConfig,
    model_path: Path | None = None,
    microphone_device_index: int | None = None,
    state_callback: Callable[[VoicePipelineState], None] | None = None,
    wake_word_threshold: float | None = None,
    state_bus: AgentStateBus | None = None,
    desktop_kill_switch: DesktopKillSwitch | None = None,
    telemetry_bus: TelemetryBus | None = None,
    audio_activity_bus: AudioActivityBus | None = None,
    conversation_log_bus: ConversationLogBus | None = None,
    gpu_budget: GpuBudgetManager | None = None,
) -> None:
    pipeline = build_voice_pipeline(
        config=config,
        model_path=model_path,
        microphone_device_index=microphone_device_index,
        state_callback=state_callback,
        wake_word_threshold=wake_word_threshold,
        state_bus=state_bus,
        desktop_kill_switch=desktop_kill_switch,
        telemetry_bus=telemetry_bus,
        audio_activity_bus=audio_activity_bus,
        conversation_log_bus=conversation_log_bus,
        gpu_budget=gpu_budget,
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



def main(argv: list[str] | None = None) -> None:
    from jarvis.runtime.cli import main as runtime_main

    runtime_main(argv)


if __name__ == "__main__":
    main()
