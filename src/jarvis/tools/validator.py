from typing import Any

from jarvis.tools.base import Tool


class ToolArgumentValidator:
    def validate(
        self,
        tool: Tool,
        arguments: dict[str, Any],
    ) -> tuple[bool, str | None]:

        schema = tool.parameters

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        allow_extra = schema.get("additionalProperties", True)

        # Verifica argumentos obrigatórios
        for argument in required:
            if argument not in arguments:
                return False, f"Argumento obrigatório ausente: '{argument}'."

        # Verifica argumentos que a Tool não conhece
        if not allow_extra:
            for argument in arguments:
                if argument not in properties:
                    return False, f"Argumento não permitido: '{argument}'."

        return True, None