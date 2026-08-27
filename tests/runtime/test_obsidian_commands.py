from jarvis.runtime.obsidian_commands import ObsidianCommandParser


def test_parser_handles_create_title_and_content_from_voice_phrasing() -> None:
    parser = ObsidianCommandParser()

    first = parser.parse(
        "Faz a anotação pra mim? Coloca o título como Eryton e escreve nela. "
        "Eryton é lindo?"
    )
    assert first is not None
    assert first.action == "create"
    assert first.reference == "Eryton"
    assert first.content == "Eryton é lindo?"

    second = parser.parse(
        "Eu quero que você crie uma nota no Obsidian com o título Eliton Lindo "
        "e no texto dentro dela diz que Eliton é gostoso."
    )
    assert second is not None
    assert second.action == "create"
    assert second.reference == "Eliton Lindo"
    assert second.content == "Eliton é gostoso."


def test_parser_handles_contextual_append_open_read_and_list() -> None:
    parser = ObsidianCommandParser()

    append = parser.parse("Dentro dessa nota, escreve pra mim, televisão UHD.")
    assert append is not None
    assert append.action == "append"
    assert append.reference is None
    assert append.content == "televisão UHD"

    open_read = parser.parse("É, agora abre para mim e lê.")
    assert open_read is not None
    assert open_read.action == "open_read"
    assert open_read.reference is None

    read = parser.parse("Lê pra minha nota, teste Jarvis.")
    assert read is not None
    assert read.action == "read"
    assert read.reference == "teste Jarvis"

    listed = parser.parse("Me diz quais notas tem no Obsidian.")
    assert listed is not None
    assert listed.action == "list"


def test_create_repairs_whisper_criou_only_with_explicit_write_clause() -> None:
    parser = ObsidianCommandParser()

    command = parser.parse(
        "Criou uma nota chamada Ellington, escreve nela Ellington é lindo."
    )

    assert command is not None
    assert command.action == "create"
    assert command.reference == "Ellington"
    assert command.content == "Ellington é lindo."


def test_criou_question_is_not_treated_as_create_command() -> None:
    parser = ObsidianCommandParser()

    assert parser.parse("Você criou uma nota chamada Ellington?") is None


def test_parser_handles_named_read_context_read_and_article() -> None:
    parser = ObsidianCommandParser()

    named = parser.parse("Leia Jarvis Tool.md")
    contextual = parser.parse("Eu quero que você leia ela.")
    with_article = parser.parse("Pronto, eu quero que você leia a Jarvis Tool.")

    assert named is not None
    assert named.action == "read"
    assert named.reference == "Jarvis Tool.md"
    assert contextual is not None
    assert contextual.action == "read"
    assert contextual.reference is None
    assert with_article is not None
    assert with_article.reference == "Jarvis Tool"


def test_parser_handles_explicit_append_then_read_voice_phrase() -> None:
    parser = ObsidianCommandParser()

    command = parser.parse(
        "Dentro de Jarvis Tool.md coloque Sabrina Linda e depois leia "
        "a nota toda para mim."
    )

    assert command is not None
    assert command.action == "append"
    assert command.reference == "Jarvis Tool.md"
    assert command.content == "Sabrina Linda"


def test_parser_handles_nested_write_phrase_with_explicit_note() -> None:
    parser = ObsidianCommandParser()

    command = parser.parse(
        "Anote em JarvisTool.md Dentro dela coloque Sabrina Linda"
    )

    assert command is not None
    assert command.action == "append"
    assert command.reference == "JarvisTool.md"
    assert command.content == "Sabrina Linda"


def test_parser_accepts_natural_list_variations_from_voice_context() -> None:
    parser = ObsidianCommandParser()

    for message in (
        "e lista minhas notas.",
        "Lista para mim novamente de forma bem rápida as anotações que eu tenho.",
        "Então lista aí minhas anotações.",
        "Liste de minhas anotações.",
        "Eu quero que você liste elas.",
    ):
        command = parser.parse(message)
        assert command is not None, message
        assert command.action == "list", message
