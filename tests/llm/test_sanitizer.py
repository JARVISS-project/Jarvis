import pytest

from jarvis.llm.sanitizer import sanitize_assistant_response


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<think>análise secreta</think>Olá.", "Olá."),
        (
            "análise vazada\n</think>\nHoje é terça-feira.",
            "Hoje é terça-feira.",
        ),
        ("<response>Olá.</response>", "Olá."),
        (
            "Eu acho que pensar (think) é útil.",
            "Eu acho que pensar (think) é útil.",
        ),
        (
            "Wait, no...\nThe assistant should call a tool.\n"
            "tool_call internal\n</think>\nResposta correta.",
            "Resposta correta.",
        ),
        ("<tool_call>{\"name\": \"delete_file\"}</tool_call>", ""),
    ],
)
def test_sanitize_assistant_response(raw: str, expected: str) -> None:
    assert sanitize_assistant_response(raw) == expected
