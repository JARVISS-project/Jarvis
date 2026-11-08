from jarvis.speech.normalizer import TranscriptNormalizer


def test_predictable_path_forms_are_normalized_without_name_correction() -> None:
    normalizer = TranscriptNormalizer()
    assert normalizer.normalize("abra notas ponto md") == "abra notas.md"
    assert normalizer.normalize("liste src barra jarvis") == "liste src/jarvis"
    assert normalizer.normalize("headme ponto md") == "headme.md"
    assert normalizer.normalize("teste underline segurança ponto txt") == (
        "teste_segurança.txt"
    )
    assert normalizer.normalize("relatório hífen final ponto md") == (
        "relatório-final.md"
    )
    assert normalizer.normalize("texto sobre underline em Python") == (
        "texto sobre underline em Python"
    )
