from pathlib import Path

from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.security.filesystem import FileSystemGuard
from jarvis.skills.mirror import ObsidianSkillMirror
from jarvis.skills.models import SkillDefinition, SkillStep


def test_obsidian_mirror_is_documentation_not_executable_source(tmp_path: Path) -> None:
    vault_dir = tmp_path / "Vault"
    vault_dir.mkdir()
    vault = ObsidianVault(vault_dir, FileSystemGuard(vault_dir))
    mirror = ObsidianSkillMirror(vault)
    skill = SkillDefinition(
        name="Spotify - Tocar música",
        original_request="toca uma música",
        steps=(SkillStep("open_application", {"name": "Spotify"}),),
    )

    path = mirror.sync(skill)
    content = vault.read_note(path)

    assert "NÃO é fonte executável" in content
    assert "[[Jarvis Skills]]" in content
    assert "[[Tool - open_application]]" in content
    assert "Skill lembra **como** executar" in content
