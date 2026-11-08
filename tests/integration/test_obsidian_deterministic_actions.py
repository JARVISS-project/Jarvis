from pathlib import Path

from jarvis.config import JarvisConfig
from jarvis.context.note_reference_resolver import NoteReferenceResolver
from jarvis.context.recent import RecentContext
from jarvis.harness import Harness
from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.obsidian import obsidian_tools
from jarvis.tools.registry import ToolRegistry


class NeverLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("LLM não deveria executar comando determinístico")


def build_harness(tmp_path: Path) -> tuple[Harness, Path, NeverLLM]:
    vault_path = tmp_path / "Vault"
    vault_path.mkdir()
    vault = ObsidianVault(
        vault_path,
        FileSystemGuard(vault_path),
        uri_opener=lambda _uri: True,
    )
    registry = ToolRegistry()
    for tool in obsidian_tools(vault):
        registry.register(tool)
    llm = NeverLLM()
    recent = RecentContext()
    from jarvis.security.policy import SecurityPolicy
    class PermissivePolicy(SecurityPolicy):
        def is_allowed(self, risk): return True
        def requires_confirmation(self, risk): return False

    safety = SafetyManager()
    safety.policy = PermissivePolicy()

    executor = ToolExecutor(
        safety,
        AuditLogger(tmp_path / "audit.log"),
        CapabilityPolicy(
            {
                Capability.OBSIDIAN_READ: True,
                Capability.OBSIDIAN_WRITE: True,
            }
        ),
    )
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
    )
    return harness, vault_path, llm


def test_create_with_spoken_title_uses_real_tool_and_adds_md(tmp_path: Path) -> None:
    harness, vault, llm = build_harness(tmp_path)

    response = harness.processar_mensagem(
        "Faz a anotação pra mim? Coloca o título como Eryton e escreve nela. "
        "Eryton é lindo?"
    )

    assert "Eryton.md" in response
    assert (vault / "Eryton.md").read_text(encoding="utf-8") == "Eryton é lindo?"
    assert [step.tool_name for step in harness.last_tool_trace] == [
        "obsidian_create_note"
    ]
    assert llm.calls == 0


def test_create_append_open_and_read_context_never_fake_actions(tmp_path: Path) -> None:
    harness, vault, llm = build_harness(tmp_path)

    harness.processar_mensagem("Cria pra mim uma nota chamada elenton.md")
    append = harness.processar_mensagem(
        "Dentro dessa nota, escreve pra mim, televisão UHD."
    )
    opened = harness.processar_mensagem("Agora abre essa nota pra mim.")
    read = harness.processar_mensagem("Agora lê essa nota pra mim.")

    assert "Conteúdo adicionado" in append
    assert "Abri" in opened
    assert "televisão UHD" in read
    assert (vault / "elenton.md").read_text(encoding="utf-8") == "televisão UHD"
    assert llm.calls == 0


def test_open_and_read_last_created_note_runs_both_tools(tmp_path: Path) -> None:
    harness, _vault, llm = build_harness(tmp_path)
    harness.processar_mensagem(
        "Eu quero que você crie uma nota no Obsidian com o título Eliton Lindo "
        "e no texto dentro dela diz que Eliton é gostoso."
    )

    response = harness.processar_mensagem("É, agora abre para mim e lê.")

    assert response == "Eliton é gostoso."
    assert [step.tool_name for step in harness.last_tool_trace] == [
        "obsidian_open_note",
        "obsidian_read_note",
    ]
    assert llm.calls == 0


def test_read_spoken_note_name_without_md_resolves_existing_note(tmp_path: Path) -> None:
    harness, vault, llm = build_harness(tmp_path)
    (vault / "Teste Jarvis.md").write_text("conteúdo real", encoding="utf-8")

    response = harness.processar_mensagem("Lê pra minha nota, teste Jarvis.")

    assert response == "conteúdo real"
    assert harness.last_tool_trace[0].tool_name == "obsidian_read_note"
    assert llm.calls == 0


class TextOnlyLLM(LLMClient):
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        from jarvis.llm.types import LLMResponse

        self.calls += 1
        return LLMResponse(self.text)


def test_llm_cannot_claim_note_action_when_no_tool_ran(tmp_path: Path) -> None:
    harness, vault, _llm = build_harness(tmp_path)
    (vault / "Existente.md").write_text("x", encoding="utf-8")
    hallucinating = TextOnlyLLM("Pronto. Abri a nota.")
    harness.llm = hallucinating

    response = harness.processar_mensagem("Abra a nota Existente no Obsidian.")

    assert response.startswith("Entendi que você quer uma ação real")
    assert hallucinating.calls == 2
    assert harness.last_tool_trace == []


def test_stt_criou_with_write_clause_creates_instead_of_appending_previous(
    tmp_path: Path,
) -> None:
    harness, vault, llm = build_harness(tmp_path)

    response = harness.processar_mensagem(
        "Criou uma nota chamada Ellington, escreve nela Ellington é lindo."
    )

    assert "Ellington.md" in response
    assert (vault / "Ellington.md").read_text(encoding="utf-8") == (
        "Ellington é lindo."
    )
    assert [step.tool_name for step in harness.last_tool_trace] == [
        "obsidian_create_note"
    ]
    assert llm.calls == 0


def test_named_and_contextual_reads_from_real_voice_examples_bypass_llm(
    tmp_path: Path,
) -> None:
    harness, vault, llm = build_harness(tmp_path)
    (vault / "Jarvis Tool.md").write_text("Sabrina Linda", encoding="utf-8")
    harness.processar_mensagem("Liste todas as notas no Obsidian")

    named = harness.processar_mensagem("Leia Jarvis Tool.md")
    contextual = harness.processar_mensagem("Eu quero que você leia ela.")
    article = harness.processar_mensagem(
        "Pronto, eu quero que você leia a Jarvis Tool."
    )

    assert named == "Sabrina Linda"
    assert contextual == "Sabrina Linda"
    assert article == "Sabrina Linda"
    assert llm.calls == 0


def test_explicit_append_and_read_compound_phrase_runs_real_tools(
    tmp_path: Path,
) -> None:
    harness, vault, llm = build_harness(tmp_path)
    (vault / "Jarvis Tool.md").write_text("", encoding="utf-8")

    response = harness.processar_mensagem(
        "Dentro de Jarvis Tool.md coloque Sabrina Linda e depois leia "
        "a nota toda para mim."
    )

    assert response == "Sabrina Linda"
    assert (vault / "Jarvis Tool.md").read_text(encoding="utf-8") == (
        "Sabrina Linda"
    )
    assert [step.tool_name for step in harness.last_tool_trace] == [
        "obsidian_append_note",
        "obsidian_read_note",
    ]
    assert llm.calls == 0


def test_create_without_spoken_title_asks_only_for_missing_title(
    tmp_path: Path,
) -> None:
    harness, _vault, llm = build_harness(tmp_path)

    response = harness.processar_mensagem(
        "Nesse caso crie uma nota aí e bota aí beta Jarvis"
    )

    assert response == "Qual nome você quer dar à nota?"
    assert llm.calls == 0


def test_natural_note_list_variations_execute_real_list_tool(tmp_path: Path) -> None:
    harness, vault, llm = build_harness(tmp_path)
    (vault / "Jarvis Tool.md").write_text("x", encoding="utf-8")
    (vault / "Teste.md").write_text("y", encoding="utf-8")

    first = harness.processar_mensagem("e lista minhas notas.")
    second = harness.processar_mensagem(
        "Lista para mim novamente de forma bem rápida as anotações que eu tenho."
    )
    third = harness.processar_mensagem("Eu quero que você liste elas.")

    for response in (first, second, third):
        assert "Jarvis Tool.md" in response
        assert "Teste.md" in response
    assert harness.last_tool_trace[0].tool_name == "obsidian_list_notes"
    assert llm.calls == 0
