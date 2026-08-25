import pytest

from jarvis.security.confirmation_intent import (
    ConfirmationIntent,
    ConfirmationInterpreter,
)


@pytest.fixture
def interpreter() -> ConfirmationInterpreter:
    return ConfirmationInterpreter()


@pytest.mark.parametrize(
    "text",
    [
        "sim", "pode", "pode fazer", "ok faça", "beleza pode fazer",
        "autorizo", "confirmo", "manda ver", "pode continuar",
        "sim, pode fazer", "ok, faça isso", "beleza, pode continuar",
        "Pode apagar.", "beleza", "vai", "pode criar", "pode editar",
    ],
)
def test_natural_confirmations(
    interpreter: ConfirmationInterpreter,
    text: str,
) -> None:
    assert interpreter.interpret(text) is ConfirmationIntent.CONFIRM


@pytest.mark.parametrize(
    "text",
    [
        "não", "nao", "cancela", "não faça", "melhor não",
        "deixa pra lá", "pare", "não autorizo", "não continue",
        "esquece", "não, pode deixar",
    ],
)
def test_natural_denials_have_priority(
    interpreter: ConfirmationInterpreter,
    text: str,
) -> None:
    assert interpreter.interpret(text) is ConfirmationIntent.DENY


@pytest.mark.parametrize(
    "text",
    [
        "talvez", "acho que sim", "provavelmente", "pode ser", "não sei",
        "se quiser", "tanto faz", "você decide",
    ],
)
def test_ambiguous_phrases_are_unknown(
    interpreter: ConfirmationInterpreter,
    text: str,
) -> None:
    assert interpreter.interpret(text) is ConfirmationIntent.UNKNOWN


def test_normalization_is_conservative(interpreter: ConfirmationInterpreter) -> None:
    assert interpreter.normalize("  OK,  PODE fazer! ") == "ok pode fazer"
    assert interpreter.interpret("headme.md") is ConfirmationIntent.UNKNOWN


def test_common_portuguese_stt_variant_is_normalized_conservatively(
    interpreter: ConfirmationInterpreter,
) -> None:
    assert interpreter.interpret("podi fazer") is ConfirmationIntent.CONFIRM
