from typing import Any

from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class EditFileTool(Tool):
    name = "edit_file"
    description = "Edita um arquivo de texto existente no diretório permitido."
    risk = RiskLevel.MEDIUM

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Caminho relativo do arquivo dentro "
                    "do diretório permitido."
                ),
            },
            "content": {
                "type": "string",
                "description": "Novo conteúdo de texto do arquivo.",
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(self, filesystem: FileSystemGuard) -> None:
        self.filesystem = filesystem

    def get_target(self, **kwargs: Any) -> str:
        return str(
            self.filesystem.resolve(kwargs["path"], for_write=True)
        )

    def preflight(self, **kwargs: Any) -> None:
        target = self.filesystem.resolve(
            kwargs["path"],
            for_write=True,
        )
        if not target.exists():
            raise FileNotFoundError(kwargs["path"])
        if not target.is_file():
            raise IsADirectoryError(
                "O caminho informado não é um arquivo."
            )

    def execute(self, **kwargs: Any) -> str:
        target = self.filesystem.resolve(kwargs["path"], for_write=True)

        if not target.exists():
            return "Arquivo não encontrado."

        if not target.is_file():
            return "O caminho informado não é um arquivo."

        target.write_text(kwargs["content"], encoding="utf-8")
        return "Arquivo editado com sucesso."
