import json
from pathlib import Path

from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.tools.registry import ToolRegistry


class NeverConfigLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("/config não deve chamar o LLM")


def build_config_harness(
    settings_path: Path,
) -> tuple[Harness, ConversationMemory, NeverConfigLLM]:
    memory = ConversationMemory()
    llm = NeverConfigLLM()
    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=object(),  # type: ignore[arg-type]
        memory=memory,
        config=JarvisConfig(settings_path=settings_path),
    )
    return harness, memory, llm


def test_config_commands_persist_only_allowed_obsidian_fields(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT", raising=False)
    monkeypatch.delenv("JARVIS_OBSIDIAN_WRITE", raising=False)
    settings_path = tmp_path / "data" / "settings.json"
    vault = tmp_path / "Meu Vault"
    vault.mkdir()
    harness, memory, llm = build_config_harness(settings_path)

    vault_response = harness.processar_mensagem(
        f'/config obsidian vault "{vault}"'
    )
    write_response = harness.processar_mensagem(
        "/config obsidian write on"
    )
    status = harness.processar_mensagem("/config obsidian")

    assert "Meu Vault" in vault_response
    assert "habilitada" in write_response
    assert "Vault: Meu Vault" in status
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "obsidian_vault": str(vault),
        "obsidian_write": True,
    }
    assert memory.get_history() == []
    assert llm.calls == 0


def test_invalid_local_config_is_reported_without_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT", raising=False)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("[]", encoding="utf-8")
    harness, memory, llm = build_config_harness(settings_path)

    response = harness.processar_mensagem("/config")

    assert "Local settings: INVALID" in response
    assert memory.get_history() == []
    assert llm.calls == 0


def test_voice_and_persona_config_are_persistent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("JARVIS_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_TTS_REFERENCE_AUDIO", raising=False)
    monkeypatch.delenv("JARVIS_ASSISTANT_ADDRESS_MODE", raising=False)
    settings_path = tmp_path / "data" / "settings.json"
    reference = tmp_path / "voz-original.wav"
    reference.write_bytes(b"RIFFfake")
    harness, memory, llm = build_config_harness(settings_path)

    provider = harness.processar_mensagem(
        "/config voice provider piper"
    )
    voice = harness.processar_mensagem(
        f'/config voice reference "{reference}"'
    )
    persona = harness.processar_mensagem(
        "/config persona treatment senhor"
    )

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["tts_provider"] == "piper"
    assert saved["tts_reference_audio"] == str(reference)
    assert saved["assistant_address_mode"] == "senhor"
    assert "piper" in provider
    assert reference.name in voice
    assert "senhor" in persona
    assert memory.get_history() == []
    assert llm.calls == 0
