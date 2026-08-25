from jarvis.context.active import ActiveContext, ContextRouter


def test_notes_override_incidental_filesystem_term() -> None:
    router = ContextRouter()

    result = router.update("Procure as minhas notas sobre testes.")

    assert result is ActiveContext.OBSIDIAN


def test_explicit_correction_has_priority_and_sticks() -> None:
    router = ContextRouter()
    assert router.update("Procure testes.") is ActiveContext.FILESYSTEM

    assert (
        router.update("Não no diretório, eu quero no Obsidian.")
        is ActiveContext.OBSIDIAN
    )
    assert router.update("Abra a segunda.") is ActiveContext.OBSIDIAN


def test_explicit_project_switches_back_to_filesystem() -> None:
    router = ContextRouter()
    router.update("Procure minhas notas sobre Jarvis.")

    assert (
        router.update("Agora procure o arquivo pyproject no projeto.")
        is ActiveContext.FILESYSTEM
    )
