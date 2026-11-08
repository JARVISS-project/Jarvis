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
        r"aplicativos|programa|programas|desktop|tela|screen|visual|"
        r"feche|fecha|fechar|encerre|encerra)\b"
    )
    _ACTION_VERBS = re.compile(
        r"\b(?:abra|abre|abrir|leia|le|ler|mostre|mostra|liste|lista|listar|"
        r"procure|procura|busque|busca|encontre|encontra|adicione|adiciona|"
        r"anote|anota|escreva|escreve|coloque|coloca|bote|bota|ponha|poe|"
        r"edite|edita|crie|cria|apague|apaga|exclua|exclui|mova|move|rode|"
        r"roda|execute|executa|teste|testa|verifique|verifica|cheque|checa|"
        r"foque|foca|focar|inicie|inicia|iniciar|pressione|pressiona|"
        r"aperte|aperta|observe|observa|capture|captura|compare|compara|"
        r"feche|fecha|fechar|encerre|encerra)\b"
    )
    _RESOURCE_WORDS = re.compile(
        r"\b(?:arquivo|arquivos|pasta|diretorio|nota|notas|obsidian|vault|"
        r"cofre|tool|tools|ferramenta|ferramentas|runtime|sistema|janela|"
        r"janelas|aplicativo|aplicativos|programa|programas|desktop|mouse|"
        r"ponteiro|cursor|tecla|escape|esc|tela|screen|visual)\b"
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
        "desktop_move_pointer_to_window",
        "desktop_press_escape",
        "desktop_close_window",
    }
    VISION = {"vision_observe_screen_change"}
    _DESKTOP_REQUEST = re.compile(
        r"\b(?:janela|janelas|aplicativo|aplicativos|programa|programas|"
        r"desktop|notepad|calculadora|calculator|explorer|"
        r"vs code|visual studio code|foco|frente|mouse|ponteiro|cursor|"
        r"tecla|escape|esc)\b"
    )
    _VISION_REQUEST = re.compile(
        r"(?:\b(?:observe|observa|observar|verifique|verifica|compare|"
        r"compara|comparar|capture|captura|capturar)\b.*"
        r"\b(?:tela|screen|visual)\b|\b(?:tela|screen)\b.*"
        r"\b(?:mudou|mudanca|alterou|diferente)\b)"
    )
    _DESKTOP_FOCUS_PATTERNS = (
        re.compile(r"(?:foque|foca|focar) (?:a )?janela (?:do |da |de )?(.+)"),
        re.compile(r"(?:vai|va) (?:pro |pra |para o |para a |ao |a )(.+)"),
        re.compile(r"(?:coloque|coloca|bote|bota) (?:a |o )?(.+) em foco"),
        re.compile(r"(?:traga|traz) (?:a |o )?(.+) (?:pra|para a) frente"),
    )
    _DESKTOP_OPEN = re.compile(
        r"(?:abra|abre|abrir|inicie|inicia|iniciar) "
        r"(?:o |a )?(?:aplicativo |programa )?(.+)"
    )
    _DESKTOP_MOVE_POINTER = re.compile(
        r"(?:mova|move|movam|leve|leva) (?:o )?(?:mouse|ponteiro|cursor) "
        r"(?:para|pro|pra) (?:o )?centro (?:da janela|da tela) "
        r"(?:do |da |de |no |na )?(.+)"
    )
    _DESKTOP_ESCAPE = re.compile(
        r"(?:pressione|pressiona|pressionem|aperte|aperta) "
        r"(?:(?:a tecla|o botao|o) )?(?:escape|esc) (?:e )?"
        r"(?:(?:na|no|em) (?:a )?janela (?:do |da |de )?|"
        r"(?:do |da |de |no |na |em )(?:janela )?)(.+)"
    )
    _DESKTOP_ESCAPE_WITHOUT_TARGET = re.compile(
        r"(?:pressione|pressiona|pressionem|aperte|aperta) "
        r"(?:(?:a tecla|o botao|o) )?(?:escape|esc)"
    )
    _DESKTOP_CLOSE = re.compile(
        r"(?:feche|fecha|fechar|encerre|encerra) "
        r"(?:(?:a )?janela (?:do |da |de )?|(?:o |a ))?(.+)"
    )
    _DESKTOP_REPEAT_POINTER = re.compile(
        r"(?:mova|move|movam) (?:o (?:mouse|ponteiro|cursor) )?novamente"
    )
    _DESKTOP_REFERENCE_PATTERNS = (
        re.compile(
            r"(?:traga|traz) (?:ela|essa janela) "
            r"(?:de volta|pra frente|para a frente)"
        ),
        re.compile(r"(?:coloque|coloca) (?:ela|essa janela) em foco"),
        re.compile(r"(?:foque|foca) (?:nela|essa janela)"),
        re.compile(r"(?:volte|volta) (?:pra|para) ela"),
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

        if self.is_vision_request(message):
            return [tool for tool in available if tool.name in self.VISION]

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
        if cls.is_desktop_reference(message):
            return True
        if cls.desktop_action(message) is not None:
            return True
        if re.search(r"\b(?:nota|notas|vault|cofre)\b", normalized):
            return False
        return cls._DESKTOP_REQUEST.search(normalized) is not None

    @classmethod
    def is_vision_request(cls, message: str) -> bool:
        return cls._VISION_REQUEST.search(cls.normalize(message)) is not None

    @classmethod
    def desktop_action(
        cls,
        message: str,
        last_window: str | None = None,
    ) -> tuple[str, dict[str, str]] | None:
        normalized = cls.normalize(message)
        normalized = re.sub(r"^(?:jarvis |por favor |e )+", "", normalized)
        normalized = re.sub(r" por favor$", "", normalized)
        if (
            "bloco de notas" not in normalized
            and re.search(r"\b(?:nota|notas|vault|cofre)\b", normalized)
        ):
            return None
        if cls._DESKTOP_REPEAT_POINTER.fullmatch(normalized):
            if last_window is None:
                return None
            return "desktop_move_pointer_to_window", {"title": last_window}
        if cls.is_desktop_reference(normalized):
            if last_window is None:
                return None
            return "desktop_focus_window", {"title": last_window}
        for pattern in cls._DESKTOP_FOCUS_PATTERNS:
            match = pattern.fullmatch(normalized)
            if match is not None:
                return "desktop_focus_window", {"title": match.group(1)}
        match = cls._DESKTOP_OPEN.fullmatch(normalized)
        if match is not None:
            return "desktop_open_application", {
                "application": match.group(1)
            }
        match = cls._DESKTOP_MOVE_POINTER.fullmatch(normalized)
        if match is not None:
            return "desktop_move_pointer_to_window", {
                "title": match.group(1)
            }
        match = cls._DESKTOP_ESCAPE.fullmatch(normalized)
        if match is not None:
            return "desktop_press_escape", {"title": match.group(1)}
        match = cls._DESKTOP_CLOSE.fullmatch(normalized)
        if match is not None:
            return "desktop_close_window", {"title": match.group(1)}
        return None

    @classmethod
    def is_desktop_reference(cls, message: str) -> bool:
        normalized = cls.normalize(message)
        return cls._DESKTOP_REPEAT_POINTER.fullmatch(normalized) is not None or any(
            pattern.fullmatch(normalized)
            for pattern in cls._DESKTOP_REFERENCE_PATTERNS
        )

    @classmethod
    def is_incomplete_desktop_input(cls, message: str) -> bool:
        normalized = cls.normalize(message)
        normalized = re.sub(r"^(?:jarvis |por favor |e )+", "", normalized)
        return cls._DESKTOP_ESCAPE_WITHOUT_TARGET.fullmatch(normalized) is not None


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
