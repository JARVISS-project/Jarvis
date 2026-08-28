import pytest

from jarvis.application.turn_policy import TurnKind, TurnPolicy


@pytest.mark.parametrize(
    "message",
    [
        "Você acha que vale colocar reconhecimento facial agora?",
        "Qual é a sua opinião sobre abrir o Obsidian no startup?",
        "Me ajude a planejar como organizar essas notas.",
        "Como eu poderia criar uma nota para este projeto?",
        "Seria uma boa ideia apagar esses arquivos antigos?",
        "Avalie se devo apagar o arquivo notas.txt.",
        "Compare apagar e arquivar notas.txt.",
        "O que seria melhor: apagar ou mover notas.txt?",
    ],
)
def test_advisory_turns_are_detected_without_llm(message: str) -> None:
    assert TurnPolicy().classify(message) is TurnKind.ADVISORY


@pytest.mark.parametrize(
    "message",
    [
        "Crie uma nota chamada Projeto.",
        "Foca a janela do Obsidian.",
        "O que você acha dessa estrutura e depois crie a nota.",
        "Qual é sua opinião e faça a alteração.",
        "Você acha que isso faz sentido? Abra o Obsidian.",
    ],
)
def test_explicit_effect_requests_remain_operational(message: str) -> None:
    assert TurnPolicy().classify(message) is TurnKind.EFFECT_REQUEST
