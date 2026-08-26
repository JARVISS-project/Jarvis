"""Construção de URIs controladas do Obsidian."""

from pathlib import Path
from urllib.parse import quote


def build_obsidian_open_uri(note_path: Path) -> str:
    """Constrói uma URI de abertura usando um caminho já validado do Vault.

    O Obsidian aceita ``path`` absoluto e isso evita depender do nome exibido
    do Vault, que pode ser diferente do nome da pasta no Windows. O chamador
    deve fornecer um ``Path`` previamente resolvido/validado pelo
    :class:`ObsidianVault`.
    """
    if not isinstance(note_path, Path):
        raise TypeError("note_path deve ser um Path previamente validado.")
    if not note_path.is_absolute():
        raise ValueError("note_path deve ser absoluto.")

    # ``as_posix`` mantém o caminho Windows estável (C:/...) e quote faz
    # percent-encoding; especialmente, espaço vira %20 (nunca '+').
    encoded_path = quote(note_path.as_posix(), safe="/:")
    return f"obsidian://open?path={encoded_path}"
