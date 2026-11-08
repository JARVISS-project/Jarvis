from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRequest:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)

    def to_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content,
        }

        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                }
                for call in self.tool_calls
            ]

        return message