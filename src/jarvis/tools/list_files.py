from typing import Any

from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class ListFilesTool(Tool):
    name = "list_files"
    description = "Lista arquivos e pastas existentes em um diretório."
    risk = RiskLevel.READ_ONLY

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Caminho relativo do diretório. "
                    "Use '.' para o diretório atual."
                ),
            }
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, filesystem: FileSystemGuard) -> None:
        self.filesystem = filesystem

    def get_target(self, **kwargs: Any) -> str:
        path = kwargs.get("path", ".")
        return str(self.filesystem.resolve(path))

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path", ".")
        target = self.filesystem.resolve(path)
        self.filesystem.require_operation(self.name, target, "read")

        if not target.exists():
            return "Diretório não encontrado."

        if not target.is_dir():
            return "O caminho informado não é um diretório."

        entries = sorted(target.iterdir())

        if not entries:
            return "O diretório está vazio."

        results = []

        for entry in entries:
            try:
                self.filesystem.resolve(str(entry))
                self.filesystem.require_operation(self.name, entry, "read")
            except PermissionError:
                continue
            tipo = "DIR" if entry.is_dir() else "FILE"
            results.append(f"[{tipo}] {entry.name}")

        return "\n".join(results)
