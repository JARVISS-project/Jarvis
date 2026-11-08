from pathlib import Path
from threading import Event
from typing import Any

from jarvis.config import JarvisConfig
from jarvis.context.active import ActiveContext
from jarvis.context.note_reference_resolver import NoteReferenceResolver
from jarvis.context.recent import RecentContext
from jarvis.harness import Harness
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse, ToolCallRequest
from jarvis.memory.conversation import ConversationMemory
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.security.audit import AuditLogger
from jarvis.security.authorization import AuthorizationGrantStore
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.confirmation import VoiceConfirmation
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.safety_manager import SafetyManager
from jarvis.speech.base import TranscriptionResult
from jarvis.tools.edit_file import EditFileTool
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.obsidian import (
    ObsidianAppendNoteTool,
    ObsidianReadNoteTool,
    ObsidianSearchNotesTool,
)
from jarvis.tools.registry import ToolRegistry
from jarvis.tts.base import SpeechPlaybackHandle
from jarvis.voice.state import VoicePipelineState


class ScriptedConversationLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        return self.responses.pop(0)


class ConfirmationListener:
    def __init__(self, operations: list[str]) -> None:
        self.operations = operations

    def stop(self) -> None:
        self.operations.append("listener.stop")

    def start(self) -> None:
        self.operations.append("listener.start")

    def reset(self) -> None:
        self.operations.append("listener.reset")


class ConfirmationTTS:
    def __init__(self, operations: list[str]) -> None:
        self.operations = operations
        self.spoken: list[str] = []

    def speak_async(self, text: str) -> SpeechPlaybackHandle:
        self.spoken.append(text)
        self.operations.append(f"tts:{text}")
        completed = Event()
        completed.set()
        return SpeechPlaybackHandle(completed, lambda: None)

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        self.operations.append(f"tts:{text}")


class PrimaryConfirmationRecorder:
    def __init__(
        self,
        grants: AuthorizationGrantStore,
        operations: list[str],
    ) -> None:
        self.grants = grants
        self.operations = operations
        self.calls = 0
        self.grants_seen: list[int] = []

    def record(self) -> list[int]:
        self.calls += 1
        self.grants_seen.append(len(self.grants._grants))
        self.operations.append(f"primary.capture:{self.calls}")
        return [1] * 3000 if self.calls == 1 else []


class RetryConfirmationRecorder:
    def __init__(
        self,
        grants: AuthorizationGrantStore,
        operations: list[str],
    ) -> None:
        self.grants = grants
        self.operations = operations
        self.calls = 0
        self.grants_seen: list[int] = []

    def record(self) -> list[int]:
        self.calls += 1
        self.grants_seen.append(len(self.grants._grants))
        self.operations.append(f"retry.capture:{self.calls}")
        return [1] * 3000


class ConfirmationSTT:
    def __init__(self) -> None:
        self.texts = ["isso", "pode"]

    def transcribe(self, audio, sample_rate):  # type: ignore[no-untyped-def]
        return TranscriptionResult(self.texts.pop(0), "pt", 0.5)


def call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse("", [ToolCallRequest(name, arguments)])


def test_complete_obsidian_conversation_quick_write_needs_no_confirmation(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "Vault"
    vault_path.mkdir()
    note = vault_path / "Teste para Jarvis.md"
    note.write_text("Conteúdo inicial.", encoding="utf-8")
    vault = ObsidianVault(vault_path, FileSystemGuard(vault_path))
    recent = RecentContext()
    registry = ToolRegistry()
    registry.register(ObsidianSearchNotesTool(vault))
    registry.register(ObsidianReadNoteTool(vault))
    registry.register(ObsidianAppendNoteTool(vault))
    registry.register(EditFileTool(FileSystemGuard(tmp_path)))

    operations: list[str] = []
    grants = AuthorizationGrantStore()
    confirmation = VoiceConfirmation(
        listener=ConfirmationListener(operations),  # type: ignore[arg-type]
        recorder=PrimaryConfirmationRecorder(grants, operations),  # type: ignore[arg-type]
        retry_recorder=RetryConfirmationRecorder(grants, operations),  # type: ignore[arg-type]
        speech_to_text=ConfirmationSTT(),  # type: ignore[arg-type]
        text_to_speech=ConfirmationTTS(operations),  # type: ignore[arg-type]
        resource_manager=ResourceManager(),
        cooldown_waiter=lambda _seconds: None,
    )
    safety = SafetyManager(confirmation, grants)
    executor = ToolExecutor(
        SafetyManager(),
        AuditLogger(tmp_path / "audit.log"),
        CapabilityPolicy({
            Capability.OBSIDIAN_READ: True,
            Capability.OBSIDIAN_WRITE: True,
            Capability.FILESYSTEM_WRITE: True,
        }),
    )
    llm = ScriptedConversationLLM([
        call("obsidian_search_notes", query="teste"),
        LLMResponse("Encontrei a nota."),
        call("obsidian_read_note", path="Teste para Jarvis"),
        LLMResponse("A nota contém conteúdo inicial."),
        call("edit_file", path="ela", content="\nJarvis é lindo"),
        LLMResponse("Conteúdo adicionado à nota."),
    ])
    harness = Harness(
        llm=llm,
        tool_registry=registry,
        tool_executor=executor,
        memory=ConversationMemory(),
        config=JarvisConfig(
            settings_path=tmp_path / "settings.json",
            obsidian_enabled=True,
            obsidian_vault_path=vault_path,
            obsidian_write_enabled=True,
        ),
        recent_context=recent,
        note_reference_resolver=NoteReferenceResolver(vault, recent),
        target_confirmation=confirmation,
    )

    harness.processar_mensagem("Procure minhas notas sobre teste.")
    harness.processar_mensagem("Leia Teste para Jarvis.")
    response = harness.processar_mensagem("Adiciona Jarvis é lindo nela.")

    assert response == "Conteúdo adicionado à nota."
    assert note.read_text(encoding="utf-8") == (
        "Conteúdo inicial.\nJarvis é lindo"
    )
    assert harness.context_router.state.active is ActiveContext.OBSIDIAN
    assert recent.get("last_active_note") == "Teste para Jarvis.md"
    assert recent.get("last_active_file") is None
    assert grants._grants == {}
    assert not operations
