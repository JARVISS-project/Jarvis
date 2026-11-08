from pathlib import Path

from jarvis.config import JarvisConfig
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.tools.get_runtime_info import GetRuntimeInfoTool


def test_runtime_info_uses_real_safe_configuration(tmp_path: Path) -> None:
    config = JarvisConfig(
        settings_path=tmp_path / "missing-settings.json",
        conversation_followup_timeout_seconds=8,
        conversation_question_timeout_seconds=15,
        conversation_followup_timeout_game_mode_seconds=4,
        model_name="modelo-local",
        stt_model="small",
        obsidian_vault_path=Path("C:/segredo/Vault"),
        obsidian_write_enabled=False,
    )
    result = GetRuntimeInfoTool(config, ResourceManager()).execute()
    assert "Follow-up normal: 8s" in result
    assert "Pergunta: 15s" in result
    assert "Follow-up Game Mode: 4s" in result
    assert "Modelo: modelo-local" in result
    assert (
        "STT: faster-whisper "
        "(profile=medium, model=medium, device=auto, compute=auto, "
        "execution=legacy)"
    ) in result
    assert "Obsidian write: DISABLED" in result
    assert "C:/segredo" not in result
