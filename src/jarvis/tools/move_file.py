from typing import Any

from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class MoveFileTool(Tool):
    name = "move_file"
    description = "Move um arquivo dentro do diretório permitido."
    risk = RiskLevel.EXTERNAL_EFFECT

    parameters = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Caminho relativo do arquivo de origem.",
            },
            "destination": {
                "type": "string",
                "description": "Caminho relativo do arquivo de destino.",
            },
        },
        "required": ["source", "destination"],
        "additionalProperties": False,
    }

    def __init__(self, filesystem: FileSystemGuard) -> None:
        self.filesystem = filesystem

    def get_target(self, **kwargs: Any) -> str:
        source = self.filesystem.resolve(kwargs["source"], for_write=True)
        destination = self.filesystem.resolve(
            kwargs["destination"],
            for_write=True,
        )
        return f"{source} -> {destination}"

    def execute(self, **kwargs: Any) -> str:
        source = self.filesystem.resolve(kwargs["source"], for_write=True)
        destination = self.filesystem.resolve(
            kwargs["destination"],
            for_write=True,
        )
        self.filesystem.require_operation(self.name, source, "write")
        self.filesystem.require_operation(self.name, source, "delete")
        self.filesystem.require_operation(self.name, destination, "write")

        if not source.exists():
            return "Arquivo de origem não encontrado."

        if not source.is_file():
            return "A origem informada não é um arquivo."

        if destination.exists():
            return "O destino já existe e não será sobrescrito."

        if not destination.parent.exists():
            return "O diretório de destino não existe."

        if not destination.parent.is_dir():
            return "O caminho pai do destino não é um diretório."

        source.rename(destination)
        return "Arquivo movido com sucesso."
