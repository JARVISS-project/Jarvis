"""Construção de URIs controladas do Obsidian."""

from urllib.parse import urlencode


def build_obsidian_open_uri(vault_name: str, note_path: str) -> str:
    query = urlencode({"vault": vault_name, "file": note_path})
    return f"obsidian://open?{query}"
