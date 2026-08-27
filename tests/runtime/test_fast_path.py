from jarvis.runtime.fast_path import (
    FastIntent,
    FastPathRouter,
    format_datetime_tool_result,
)


def test_time_question_uses_fast_path_only_when_unambiguous() -> None:
    router = FastPathRouter()

    assert router.match("Me diz aí, que horas são?").intent is FastIntent.TIME
    assert router.match("E que horas são?").intent is FastIntent.TIME
    assert router.match("Então, me diz aí que horas são?").intent is FastIntent.TIME
    assert router.match("Estou perguntando que horas são.").intent is FastIntent.TIME
    assert router.match("Eu perguntei que odas são.").intent is FastIntent.TIME
    assert router.match("E odas são.").intent is FastIntent.TIME
    assert router.match("E odes são.").intent is FastIntent.TIME
    assert router.match("Que odes são?").intent is FastIntent.TIME
    assert router.match("Beleza, qual é a data de hoje?").intent is FastIntent.DATE
    assert router.match("Qual é a data de hoje?").intent is FastIntent.DATE
    assert router.match("Quantas horas eu devo estudar?") is None
    assert router.match("Odas são poemas líricos.") is None


def test_datetime_tool_result_is_formatted_for_speech() -> None:
    content = (
        "Data: 26/08/2026\n"
        "Hora: 10:36\n"
        "Timezone: -03:00\n"
        "ISO: 2026-08-26T10:36:00-03:00"
    )

    assert format_datetime_tool_result(content, FastIntent.TIME) == (
        "Agora são 10 horas e 36 minutos."
    )
    assert format_datetime_tool_result(content, FastIntent.DATE) == (
        "Hoje é 26 de agosto de 2026."
    )
