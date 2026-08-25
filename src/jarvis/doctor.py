"""Diagnósticos locais leves que não inicializam recursos pesados."""

from importlib.util import find_spec

from jarvis.config import JarvisConfig
from jarvis.memory.store import MemoryStore
from jarvis.runtime.resource_manager import ResourceManager


class JarvisDoctor:
    def __init__(
        self,
        config: JarvisConfig,
        resource_manager: ResourceManager,
        memory_store: MemoryStore,
    ) -> None:
        self.config = config
        self.resource_manager = resource_manager
        self.memory_store = memory_store

    def report(self) -> str:
        database_ok = self.config.database_path.parent.exists()
        vault = self.config.obsidian_vault_path
        obsidian_configured = (
            self.config.obsidian_enabled
            and vault is not None
            and vault.is_dir()
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
            ("TTS", self._optional_status("pyttsx3")),
            (
                "Microphone config",
                "OK" if self.config.microphone_device_index is not None
                else "NOT CONFIGURED",
            ),
            (
                "Obsidian",
                "OK" if obsidian_configured else "NOT CONFIGURED",
            ),
        ]
        if obsidian_configured and vault is not None:
            checks.extend(
                [
                    ("Vault", vault.name or "CONFIGURED"),
                    (
                        "Read",
                        "ENABLED"
                        if self.config.obsidian_read_enabled
                        else "DISABLED",
                    ),
                    (
                        "Write",
                        "ENABLED"
                        if self.config.obsidian_write_enabled
                        else "DISABLED",
                    ),
                ]
            )
        lines = ["Jarvis Doctor", ""]
        lines.extend(f"{name:.<22} {status}" for name, status in checks)
        return "\n".join(lines)

    @staticmethod
    def _optional_status(module: str) -> str:
        return "INSTALLED" if find_spec(module) is not None else "NOT INSTALLED"
