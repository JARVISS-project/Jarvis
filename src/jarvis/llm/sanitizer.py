"""Compatibilidade com a antiga API de sanitização."""

from jarvis.llm.firewall import FinalResponseFirewall

def sanitize_assistant_response(text: str) -> str:
    """Compatibilidade: retorna somente texto aprovado pela firewall."""
    validated = FinalResponseFirewall().validate(text)
    if not validated.accepted:
        return ""
    return validated.text


sanitize_final_response = sanitize_assistant_response
