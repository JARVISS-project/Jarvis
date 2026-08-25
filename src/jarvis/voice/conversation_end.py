"""Encerramento determinístico da sessão conversacional."""

from jarvis.security.confirmation_intent import ConfirmationInterpreter


class ConversationEndInterpreter:
    PHRASES = (
        "beleza valeu",
        "muito obrigado",
        "obrigado",
        "ate mais",
        "pode descansar",
        "encerrar conversa",
        "encerra conversa",
        "valeu",
    )

    def should_end(self, text: str) -> bool:
        normalized = ConfirmationInterpreter.normalize(text)
        padded = f" {normalized} "
        return any(f" {phrase} " in padded for phrase in self.PHRASES)
