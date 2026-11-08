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

        types = {
            "string": lambda value: isinstance(value, str),
            "boolean": lambda value: type(value) is bool,
            "integer": lambda value: type(value) is int,
            "number": lambda value: type(value) in (int, float),
            "array": lambda value: isinstance(value, list),
            "object": lambda value: isinstance(value, dict),
        }
        for argument, value in arguments.items():
            rule = properties.get(argument, {})
            expected = rule.get("type")
            if expected in types and not types[expected](value):
                return False, f"Tipo inválido para '{argument}': esperado {expected}."
            if "enum" in rule and value not in rule["enum"]:
                return False, f"Valor inválido para '{argument}'."

        return True, None
