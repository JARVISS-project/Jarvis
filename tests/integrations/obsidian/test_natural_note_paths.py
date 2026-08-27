from pathlib import Path

import pytest

from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.security.filesystem import FileSystemGuard


def test_bare_note_title_implicitly_uses_markdown_suffix(tmp_path: Path) -> None:
    vault_path = tmp_path / "Vault"
    vault_path.mkdir()
    vault = ObsidianVault(vault_path, FileSystemGuard(vault_path))

    vault.create_note("Sem Extensao", "conteudo")

    assert vault.read_note("Sem Extensao") == "conteudo"
    assert (vault_path / "Sem Extensao.md").is_file()
    with pytest.raises(PermissionError):
        vault.create_note("nota.txt", "x")
