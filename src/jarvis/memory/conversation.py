from typing import Literal, TypedDict


class ConversationMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class ConversationMemory:
    def __init__(self, max_messages: int = 20) -> None:
        if max_messages < 1:
            raise ValueError("max_messages deve ser maior que zero.")

        self.max_messages = max_messages
        self._messages: list[ConversationMessage] = []

    def add_user_message(self, content: str) -> None:
        self._add_message(role="user", content=content)

    def add_assistant_message(self, content: str) -> None:
        self._add_message(role="assistant", content=content)

    def get_history(self) -> list[ConversationMessage]:
        return [message.copy() for message in self._messages]

    def count(self) -> int:
        return len(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def _add_message(
        self,
        role: Literal["user", "assistant"],
        content: str,
    ) -> None:
        self._messages.append({"role": role, "content": content})

        if len(self._messages) > self.max_messages:
            excess = len(self._messages) - self.max_messages
            del self._messages[:excess]
