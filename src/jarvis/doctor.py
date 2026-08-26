"""Diagnósticos locais leves que não inicializam recursos pesados."""

from importlib.util import find_spec

from jarvis.config import JarvisConfig
from jarvis.memory.store import MemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.tools.registry import ToolRegistry


class JarvisDoctor:
    def __init__(
        self,
        config: JarvisConfig,
        resource_manager: ResourceManager,
        memory_store: MemoryStore,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config
        self.resource_manager = resource_manager
        self.memory_store = memory_store
        self.tool_registry = tool_registry

    def report(self) -> str:
        database_ok = self.config.database_path.parent.exists()
        vault = self.config.obsidian_vault_path
        obsidian_configured = (
            self.config.obsidian_enabled
            and vault is not None
            and vault.is_dir()
        )
        tools_registered = self.tool_registry is not None and all(
            self.tool_registry.get(name) is not None
            for name in ("obsidian_search_notes", "obsidian_read_note")
        )
        checks = [
            ("Runtime", "OK"),
            (
                "Game Mode",
                "ON" if self.resource_manager.is_game_mode_active() else "OFF",
            ),
            ("Memory", "OK"),
            ("Database", "OK" if database_ok else "NOT CONFIGURED"),
            ("Ollama config", "OK" if self.config.model_name else "NOT CONFIGURED"),
            (
                "Wake Word",
                "OK" if self.config.wake_word_model else "NOT CONFIGURED",
            ),
            ("STT", self._optional_status("faster_whisper")),
            ("TTS provider", self.config.tts_provider),
            ("SAPI TTS", self._optional_status("pyttsx3")),
            ("Neural TTS", self._neural_tts_status()),
            (
                "Microphone config",
                "OK" if self.config.microphone_device_index is not None
                else "NOT CONFIGURED",
            ),
            (
                "Obsidian",
                "OK" if obsidian_configured else "NOT CONFIGURED",
            ),
            ("Vault", vault.name if vault is not None else "NOT CONFIGURED"),
            (
                "Read",
                "ENABLED" if self.config.obsidian_read_enabled
                else "DISABLED",
            ),
            (
                "Write",
                "ENABLED" if self.config.obsidian_write_enabled
                else "DISABLED",
            ),
            ("Tools registered", "YES" if tools_registered else "NO"),
        ]
        if self.config.settings_error is not None:
            checks.append(("Local settings", "INVALID"))
        lines = ["Jarvis Doctor", ""]
        lines.extend(f"{name:.<22} {status}" for name, status in checks)
        return "\n".join(lines)

    @staticmethod
    def _optional_status(module: str) -> str:
        return "INSTALLED" if find_spec(module) is not None else "NOT INSTALLED"

    @staticmethod
    def _neural_tts_status() -> str:
        return (
            "INSTALLED"
            if find_spec("chatterbox") is not None
            and find_spec("sounddevice") is not None
            else "NOT INSTALLED"
        )
