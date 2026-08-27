"""Diagnósticos locais leves que não inicializam recursos pesados."""

from importlib.util import find_spec

from jarvis.config import JarvisConfig
from jarvis.memory.store import MemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.skills.store import SkillStore
from jarvis.tools.registry import ToolRegistry


class JarvisDoctor:
    def __init__(
        self,
        config: JarvisConfig,
        resource_manager: ResourceManager,
        memory_store: MemoryStore,
        tool_registry: ToolRegistry | None = None,
        skill_store: SkillStore | None = None,
    ) -> None:
        self.config = config
        self.resource_manager = resource_manager
        self.memory_store = memory_store
        self.tool_registry = tool_registry
        self.skill_store = skill_store

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
            ("Skills", self._skills_status()),
        ]
        if self.config.settings_error is not None:
            checks.append(("Local settings", "INVALID"))
        lines = ["Jarvis Doctor", ""]
        lines.extend(f"{name:.<22} {status}" for name, status in checks)
        return "\n".join(lines)


    def voice_summary(self) -> str:
        """Return a compact status sentence suitable for TTS."""

        vault = self.config.obsidian_vault_path
        obsidian_ok = bool(
            self.config.obsidian_enabled
            and vault is not None
            and vault.is_dir()
        )
        issues: list[str] = []
        if not self.config.database_path.parent.exists():
            issues.append("banco de dados")
        if not self.config.model_name:
            issues.append("Ollama")
        if not self.config.wake_word_model:
            issues.append("wake word")
        if self._optional_status("faster_whisper") != "INSTALLED":
            issues.append("STT")
        if not obsidian_ok:
            issues.append("Obsidian")
        if self.config.settings_error is not None:
            issues.append("configuração local")

        if issues:
            joined = ", ".join(issues[:-1])
            if len(issues) > 1:
                joined += f" e {issues[-1]}"
            else:
                joined = issues[0]
            return f"Encontrei atenção necessária em: {joined}."

        provider = self.config.tts_provider
        game_mode = (
            "ligado"
            if self.resource_manager.is_game_mode_active()
            else "desligado"
        )
        microphone = (
            f"microfone {self.config.microphone_device_index}"
            if self.config.microphone_device_index is not None
            else "microfone padrão do sistema"
        )
        return (
            "Tudo certo por aqui. Runtime, memória, Ollama, wake word, STT, "
            f"TTS {provider} e Obsidian estão operacionais. Estou usando o "
            f"{microphone}, e o Game Mode está {game_mode}."
        )

    def _skills_status(self) -> str:
        if self.skill_store is None:
            return "0"
        try:
            return str(len(self.skill_store.list()))
        except RuntimeError:
            return "INVALID"

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
