from typing import Any

from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Exclui um arquivo dentro do diretório permitido."
    risk = RiskLevel.HIGH

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Caminho relativo do arquivo dentro "
                    "do diretório permitido."
                ),
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    WILDCARD_CHARACTERS = "*?[]"

    def __init__(self, filesystem: FileSystemGuard) -> None:
        self.filesystem = filesystem

    def _resolve_path(self, path: str):
        if any(character in path for character in self.WILDCARD_CHARACTERS):
            raise PermissionError("Wildcards não são permitidos.")

        return self.filesystem.resolve(path, for_write=True)

    def get_target(self, **kwargs: Any) -> str:
        return str(self._resolve_path(kwargs["path"]))

    def preflight(self, **kwargs: Any) -> None:
        target = self._resolve_path(kwargs["path"])
        if not target.exists():
            raise FileNotFoundError(kwargs["path"])
        if not target.is_file():
            raise IsADirectoryError(
                "O caminho informado não é um arquivo."
            )

    def execute(self, **kwargs: Any) -> str:
        target = self._resolve_path(kwargs["path"])

        if not target.exists():
            return "Arquivo não encontrado."

        if not target.is_file():
            return "O caminho informado não é um arquivo."

        target.unlink()
        return "Arquivo excluído com sucesso."
