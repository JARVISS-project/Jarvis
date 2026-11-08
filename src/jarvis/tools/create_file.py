from typing import Any

from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class CreateFileTool(Tool):
    name = "create_file"
    description = (
        "Cria um novo arquivo de texto dentro do diretório permitido."
    )
    risk = RiskLevel.EXTERNAL_EFFECT

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Caminho relativo do novo arquivo dentro "
                    "do diretório permitido."
                ),
            },
            "content": {
                "type": "string",
                "description": "Conteúdo de texto a ser escrito no arquivo.",
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(self, filesystem: FileSystemGuard) -> None:
        self.filesystem = filesystem

    def get_target(self, **kwargs: Any) -> str:
        path = kwargs["path"]
        return str(self.filesystem.resolve(path, for_write=True))

    def execute(self, **kwargs: Any) -> str:
        path = kwargs["path"]
        content = kwargs["content"]
        target = self.filesystem.resolve(path, for_write=True)
        self.filesystem.require_operation(self.name, target, "write")

        if target.exists():
            return "O arquivo já existe e não será sobrescrito."

        if not target.parent.exists():
            return "O diretório pai não existe."

        if not target.parent.is_dir():
            return "O caminho pai informado não é um diretório."

        try:
            with target.open("x", encoding="utf-8") as file:
                file.write(content)
        except FileExistsError:
            return "O arquivo já existe e não será sobrescrito."

        return "Arquivo criado com sucesso."
