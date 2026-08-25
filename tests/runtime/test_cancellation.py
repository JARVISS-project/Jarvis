import pytest

from jarvis.runtime.cancellation import (
    CancellationController,
    CancellationInterpreter,
)


@pytest.mark.parametrize(
    "phrase",
    [
        "cancela", "deixa pra lá", "pare", "não precisa",
        "não, pode deixar", "pode parar", "aborta", "esquece isso",
    ],
)
def test_cancellation_phrases_are_deterministic(phrase: str) -> None:
    assert CancellationInterpreter().is_cancellation(phrase)


def test_controller_can_be_reset_for_next_interaction() -> None:
    controller = CancellationController()
    assert not controller.is_cancelled
    controller.request_cancel()
    assert controller.is_cancelled
    controller.clear()
    assert not controller.is_cancelled


def test_para_only_cancels_when_it_is_the_stop_order() -> None:
    interpreter = CancellationInterpreter()
    assert interpreter.is_cancellation("para")
    assert interpreter.is_cancellation("para agora")
    assert interpreter.is_cancellation("pode parar")
    assert not interpreter.is_cancellation("procure isso para mim")
    assert not interpreter.is_cancellation("isso é para amanhã")
