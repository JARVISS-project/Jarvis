import pytest

from jarvis.security.confirmation_intent import ConfirmationInterpreter
from jarvis.security.confirmation_request import ConfirmationPurpose


@pytest.mark.parametrize(
    "phrase",
    [
        "isso", "isso mesmo", "é esse", "é esse mesmo", "esse mesmo",
        "exatamente", "correto", "certinho", "sim, esse mesmo",
        "sim, é esse mesmo", "é esse aí",
    ],
)
def test_natural_target_confirmation_is_contextual(phrase: str) -> None:
    result = ConfirmationInterpreter().interpret_result(
        phrase, ConfirmationPurpose.TARGET
    )
    assert result.target_confirmed
    assert not result.action_authorized


@pytest.mark.parametrize(
    "phrase",
    [
        "sim, esse mesmo, pode fazer",
        "é esse aí, pode apagar",
        "isso mesmo, manda ver",
        "é esse, pode excluir",
    ],
)
def test_compound_phrase_confirms_target_and_action(phrase: str) -> None:
    result = ConfirmationInterpreter().interpret_result(
        phrase, ConfirmationPurpose.TARGET
    )
    assert result.target_confirmed
    assert result.action_authorized


@pytest.mark.parametrize(
    "phrase",
    ["pode fazer", "ok, faça", "manda ver", "pode apagar", "sim", "autorizo", "isso"],
)
def test_natural_security_confirmation(phrase: str) -> None:
    result = ConfirmationInterpreter().interpret_result(
        phrase, ConfirmationPurpose.SECURITY
    )
    assert result.action_authorized


@pytest.mark.parametrize(
    "phrase",
    [
        "não, esse não", "é esse, mas não apaga",
        "sim, mas deixa pra lá", "não faça",
    ],
)
def test_denial_has_absolute_priority(phrase: str) -> None:
    result = ConfirmationInterpreter().interpret_result(
        phrase, ConfirmationPurpose.TARGET
    )
    assert result.denied
    assert not result.action_authorized


def test_confirmation_words_without_pending_context_do_not_authorize() -> None:
    result = ConfirmationInterpreter().interpret_result("pode fazer", None)
    assert result.ambiguous
    assert not result.action_authorized
