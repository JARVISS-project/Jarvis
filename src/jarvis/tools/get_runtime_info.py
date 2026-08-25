"""Informações seguras da configuração efetiva do runtime."""

from typing import Any

from jarvis.config import JarvisConfig
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class GetRuntimeInfoTool(Tool):
    name = "get_runtime_info"
    description = (
        "Consulta configuração real de timeouts, Game Mode, modelo, STT "
        "e capacidades do Obsidian. Use para perguntas sobre o runtime."
    )
    risk = RiskLevel.SAFE
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(
        self,
        config: JarvisConfig,
        resource_manager: ResourceManager,
    ) -> None:
        self.config = config
        self.resource_manager = resource_manager

    def get_target(self, **kwargs: Any) -> str:
        return "runtime local"

    def execute(self, **kwargs: Any) -> str:
        game_mode = self.resource_manager.is_game_mode_active()
        return (
            f"Estado: {self.resource_manager.state.name}\n"
            f"Game Mode: {'ON' if game_mode else 'OFF'}\n"
            "Follow-up normal: "
            f"{self.config.conversation_followup_timeout_seconds:g}s\n"
            "Pergunta: "
            f"{self.config.conversation_question_timeout_seconds:g}s\n"
            "Follow-up Game Mode: "
            f"{self.config.conversation_followup_timeout_game_mode_seconds:g}s\n"
            f"Modelo: {self.config.model_name}\n"
            f"STT: faster-whisper ({self.config.stt_model})\n"
            "Obsidian read: "
            f"{'ENABLED' if self.config.obsidian_read_enabled else 'DISABLED'}\n"
            "Obsidian write: "
            f"{'ENABLED' if self.config.obsidian_write_enabled else 'DISABLED'}"
        )
