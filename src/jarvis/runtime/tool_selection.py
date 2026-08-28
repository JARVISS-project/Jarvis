from __future__ import annotations

import re
import unicodedata

from jarvis.context.active import ActiveContext
from jarvis.tools.base import Tool


class ToolSelectionRouter:
    """Conservatively reduce tool schemas for obvious conversational turns."""

    _ACTION_WORDS = re.compile(
        r"\b(?:abra|abre|abrir|leia|le|ler|mostre|mostra|liste|lista|listar|"
        r"procure|procura|buscar|busque|busca|encontre|encontra|adicione|"
        r"adiciona|adicionar|anote|anota|anotar|escreva|escreve|escrever|"
        r"coloque|coloca|bote|bota|ponha|poe|edite|edita|editar|crie|cria|"
        r"criar|apague|apaga|apagar|exclua|exclui|excluir|mova|move|mover|"
        r"rode|roda|execute|executa|teste|testa|verifique|verifica|cheque|"
        r"checa|status|arquivo|pasta|diretorio|nota|obsidian|vault|cofre|"
        r"tool|tools|ferramenta|ferramentas|janela|janelas|aplicativo|"
        r"aplicativos|programa|programas|desktop)\b"
    )
    _ACTION_VERBS = re.compile(
        r"\b(?:abra|abre|abrir|leia|le|ler|mostre|mostra|liste|lista|listar|"
        r"procure|procura|busque|busca|encontre|encontra|adicione|adiciona|"
        r"anote|anota|escreva|escreve|coloque|coloca|bote|bota|ponha|poe|"
        r"edite|edita|crie|cria|apague|apaga|exclua|exclui|mova|move|rode|"
        r"roda|execute|executa|teste|testa|verifique|verifica|cheque|checa|"
        r"foque|foca|focar|inicie|inicia|iniciar)\b"
    )
    _RESOURCE_WORDS = re.compile(
        r"\b(?:arquivo|arquivos|pasta|diretorio|nota|notas|obsidian|vault|"
        r"cofre|tool|tools|ferramenta|ferramentas|runtime|sistema|janela|"
        r"janelas|aplicativo|aplicativos|programa|programas|desktop)\b"
    )
    _PURE_CONVERSATION = re.compile(
        r"^(?:me explica|explique|o que e|o que sao|quem e|quem sao|"
        r"por que|porque|como funciona|qual a diferenca|fale sobre|"
        r"me conte|defina|resuma)\b"
    )

    FILESYSTEM = {
        "list_files", "read_file", "create_file", "edit_file",
        "delete_file", "move_file",
    }
    UTILITIES = {"get_datetime", "get_runtime_info"}
    DESKTOP = {
        "desktop_list_windows",
        "desktop_list_applications",
        "desktop_focus_window",
        "desktop_open_application",
    }
    _DESKTOP_REQUEST = re.compile(
        r"\b(?:janela|janelas|aplicativo|aplicativos|programa|programas|"
        r"desktop|notepad|calculadora|calculator|explorer)\b"
    )

    def select(
        self,
        message: str,
        active: ActiveContext,
        available: list[Tool],
    ) -> list[Tool]:
        normalized = self.normalize(message)
        if (
            self._PURE_CONVERSATION.search(normalized)
            and not self._ACTION_WORDS.search(normalized)
        ):
            return []

        if self.is_desktop_request(message):
            return [
                tool for tool in available
                if tool.name in self.DESKTOP or tool.name in self.UTILITIES
            ]

        if active is ActiveContext.OBSIDIAN or re.search(
            r"\b(?:nota|notas|obsidian|vault|cofre)\b", normalized
        ):
            return [
                tool for tool in available
                if tool.name.startswith("obsidian_") or tool.name in self.UTILITIES
            ]

        if active is ActiveContext.FILESYSTEM or re.search(
            r"\b(?:arquivo|arquivos|pasta|diretorio|projeto|pyproject|src|tests)\b",
            normalized,
        ):
            return [
                tool for tool in available
                if tool.name in self.FILESYSTEM or tool.name in self.UTILITIES
            ]

        # Unknown action: keep the full set rather than hiding a needed ability.
        return available

    @classmethod
    def is_desktop_request(cls, message: str) -> bool:
        normalized = cls.normalize(message)
        if re.search(r"\b(?:nota|notas|vault|cofre)\b", normalized):
            return False
        return cls._DESKTOP_REQUEST.search(normalized) is not None


    def looks_like_action(
        self,
        message: str,
        active: ActiveContext = ActiveContext.NONE,
    ) -> bool:
        """Detecta pedidos de ação para permitir uma segunda tentativa do LLM."""

        normalized = self.normalize(message)
        if self._ACTION_VERBS.search(normalized) is None:
            return False
        if active is not ActiveContext.NONE:
            return True
        return self._RESOURCE_WORDS.search(normalized) is not None

    @staticmethod
    def normalize(message: str) -> str:
        value = unicodedata.normalize("NFKD", message.casefold())
        value = "".join(char for char in value if not unicodedata.combining(char))
        return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())
