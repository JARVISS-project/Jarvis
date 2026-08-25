from jarvis.config import JarvisConfig
from jarvis.doctor import JarvisDoctor
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.tools.registry import ToolRegistry


class NeverLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("Doctor não deve chamar LLM")


def test_doctor_is_local_safe_and_outside_conversation() -> None:
    llm = NeverLLM()
    memory = ConversationMemory()
    harness = Harness(
        llm=llm,
        tool_registry=ToolRegistry(),
        tool_executor=object(),  # type: ignore[arg-type]
        memory=memory,
    )
    report = harness.processar_mensagem("/doctor")
    assert report.startswith("Jarvis Doctor")
    assert "Runtime" in report
    assert "STT" in report
    assert "Obsidian" in report
    assert llm.calls == 0
    assert memory.get_history() == []
    assert "token" not in report.casefold()
    assert ".env" not in report.casefold()


def test_doctor_reports_safe_obsidian_status(tmp_path) -> None:
    vault = tmp_path / "Meu Vault"
    vault.mkdir()
    doctor = JarvisDoctor(
        JarvisConfig(
            obsidian_enabled=True,
            obsidian_vault_path=vault,
            obsidian_write_enabled=False,
        ),
        ResourceManager(),
        InMemoryMemoryStore(),
    )
    report = doctor.report()
    assert "Obsidian.............. OK" in report
    assert "Vault................. Meu Vault" in report
    assert "Read.................. ENABLED" in report
    assert "Write................. DISABLED" in report
    assert str(tmp_path) not in report
