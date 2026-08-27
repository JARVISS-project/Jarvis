"""Tools expostas para a integração Markdown do Obsidian."""

from typing import Any

from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


PATH_PARAMETER = {
    "type": "string",
    "description": "Título/caminho relativo da nota no Vault; .md é opcional.",
}


class _ObsidianTool(Tool):
    def __init__(self, vault: ObsidianVault) -> None:
        self.vault = vault


class ObsidianListNotesTool(_ObsidianTool):
    name = "obsidian_list_notes"
    description = "Lista recursivamente as notas Markdown do Vault Obsidian."
    risk = RiskLevel.SAFE
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def get_target(self, **kwargs: Any) -> str:
        return str(self.vault.vault_path)

    def execute(self, **kwargs: Any) -> str:
        notes = self.vault.list_notes()
        return "\n".join(notes) if notes else "Nenhuma nota encontrada."


class ObsidianReadNoteTool(_ObsidianTool):
    name = "obsidian_read_note"
    description = "Lê uma nota Markdown específica do Vault Obsidian."
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {"path": PATH_PARAMETER},
        "required": ["path"],
        "additionalProperties": False,
    }

    def get_target(self, **kwargs: Any) -> str:
        return str(self.vault.resolve_note(kwargs["path"]))

    def preflight(self, **kwargs: Any) -> None:
        self.vault._existing_note(kwargs["path"])

    def execute(self, **kwargs: Any) -> str:
        return self.vault.read_note(kwargs["path"])


class ObsidianSearchNotesTool(_ObsidianTool):
    name = "obsidian_search_notes"
    description = "Busca texto no nome e no conteúdo das notas do Obsidian."
    risk = RiskLevel.SAFE
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Texto buscado."}
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def get_target(self, **kwargs: Any) -> str:
        return str(self.vault.vault_path)

    def execute(self, **kwargs: Any) -> str:
        results = self.vault.search_notes(kwargs["query"])
        if not results:
            return "Nenhuma nota encontrada."
        return "\n".join(
            f"- {result.path} | score={result.score} | {result.snippet}"
            for result in results
        )


class ObsidianOpenNoteTool(_ObsidianTool):
    name = "obsidian_open_note"
    description = "Abre uma nota válida no aplicativo Obsidian via URI segura."
    risk = RiskLevel.LOW
    parameters = ObsidianReadNoteTool.parameters

    def get_target(self, **kwargs: Any) -> str:
        return str(self.vault.resolve_note(kwargs["path"]))

    def preflight(self, **kwargs: Any) -> None:
        self.vault._existing_note(kwargs["path"])

    def execute(self, **kwargs: Any) -> str:
        self.vault.open_note(kwargs["path"])
        return "Nota aberta no Obsidian."


class ObsidianCreateNoteTool(_ObsidianTool):
    name = "obsidian_create_note"
    description = "Cria uma nova nota Markdown no Vault Obsidian."
    # Escrita aditiva/reversível e confinada ao Vault: não interrompe anotações rápidas.
    # CapabilityPolicy(OBSIDIAN_WRITE) continua sendo a barreira de habilitação.
    risk = RiskLevel.LOW
    parameters = {
        "type": "object",
        "properties": {
            "path": PATH_PARAMETER,
            "content": {"type": "string", "description": "Conteúdo Markdown."},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def get_target(self, **kwargs: Any) -> str:
        return str(self.vault.resolve_note(kwargs["path"], for_write=True))

    def preflight(self, **kwargs: Any) -> None:
        target = self.vault.resolve_note(kwargs["path"], for_write=True)
        if target.exists():
            raise FileExistsError(kwargs["path"])

    def execute(self, **kwargs: Any) -> str:
        self.vault.create_note(kwargs["path"], kwargs["content"])
        return "Nota criada com sucesso."


class ObsidianAppendNoteTool(_ObsidianTool):
    name = "obsidian_append_note"
    description = "Adiciona conteúdo ao final de uma nota Markdown existente."
    # Append não apaga nem sobrescreve conteúdo e continua restrito ao Vault.
    # É LOW para permitir anotações rápidas sem confirmação a cada pedido.
    risk = RiskLevel.LOW
    parameters = ObsidianCreateNoteTool.parameters

    def get_target(self, **kwargs: Any) -> str:
        return str(self.vault.resolve_note(kwargs["path"], for_write=True))

    def preflight(self, **kwargs: Any) -> None:
        self.vault._existing_note(kwargs["path"], for_write=True)

    def execute(self, **kwargs: Any) -> str:
        self.vault.append_note(kwargs["path"], kwargs["content"])
        return "Conteúdo adicionado à nota."


class ObsidianDeleteNoteTool(_ObsidianTool):
    name = "obsidian_delete_note"
    description = "Exclui uma nota Markdown específica do Vault Obsidian."
    risk = RiskLevel.HIGH
    parameters = ObsidianReadNoteTool.parameters

    def get_target(self, **kwargs: Any) -> str:
        return str(self.vault.resolve_note(kwargs["path"], for_write=True))

    def preflight(self, **kwargs: Any) -> None:
        self.vault._existing_note(kwargs["path"], for_write=True)

    def execute(self, **kwargs: Any) -> str:
        self.vault.delete_note(kwargs["path"])
        return "Nota excluída com sucesso."


def obsidian_tools(vault: ObsidianVault) -> list[Tool]:
    return [
        ObsidianListNotesTool(vault),
        ObsidianReadNoteTool(vault),
        ObsidianSearchNotesTool(vault),
        ObsidianOpenNoteTool(vault),
        ObsidianCreateNoteTool(vault),
        ObsidianAppendNoteTool(vault),
        ObsidianDeleteNoteTool(vault),
    ]
