from typing import Any

from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class ReadFileTool(Tool):
    name = "read_file"
    description = "Lê o conteúdo de um arquivo de texto."
    risk = RiskLevel.LOW

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

    MAX_FILE_SIZE = 1_000_000

    def __init__(self, filesystem: FileSystemGuard) -> None:
        self.filesystem = filesystem

    def get_target(self, **kwargs: Any) -> str:
        path = kwargs["path"]
        return str(self.filesystem.resolve(path))

    def execute(self, **kwargs: Any) -> str:
        path = kwargs["path"]
        target = self.filesystem.resolve(path)

        if not target.exists():
            return "Arquivo não encontrado."

        if not target.is_file():
            return "O caminho informado não é um arquivo."

        if target.stat().st_size > self.MAX_FILE_SIZE:
            return "Arquivo muito grande para leitura."

        try:
            return target.read_text(encoding="utf-8")

        except UnicodeDecodeError:
            return "O arquivo não parece ser um arquivo de texto UTF-8."