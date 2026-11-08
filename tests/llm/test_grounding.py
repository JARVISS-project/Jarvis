from jarvis.llm.grounding import ExecutionClaimGuard


def test_execution_claim_is_rejected_without_tool_result() -> None:
    guard = ExecutionClaimGuard()

    decision = guard.validate(
        "Encontrei os arquivos no diretório e listei as notas.",
        tool_executed=False,
    )

    assert decision.accepted is False
    assert decision.reason == "execution_claim_without_tool"


def test_execution_claim_is_allowed_after_real_tool() -> None:
    guard = ExecutionClaimGuard()

    decision = guard.validate(
        "Pronto. Criei a nota e li o conteúdo.",
        tool_executed=True,
    )

    assert decision.accepted is True


def test_normal_conversation_is_not_blocked() -> None:
    guard = ExecutionClaimGuard()

    decision = guard.validate(
        "Posso explicar como uma Tool funciona sem executar nada.",
        tool_executed=False,
    )

    assert decision.accepted is True


def test_organization_claim_is_rejected_without_edit_tool() -> None:
    guard = ExecutionClaimGuard()

    decision = guard.validate(
        "Agora está mais limpa e organizada. Pronto.",
        tool_executed=False,
    )

    assert decision.accepted is False
    assert decision.reason == "result_claim_without_tool"


def test_desktop_state_claim_is_rejected_without_successful_tool() -> None:
    guard = ExecutionClaimGuard()

    for response in (
        "Trouxe a janela para frente.",
        "A janela está em primeiro plano.",
        "O aplicativo está aberto.",
        "Pressionei Escape.",
        "O ponteiro ficou no centro.",
        "O ponteiro está posicionado no centro.",
        "A tela mudou.",
        'O ponteiro está agora na janela "Bloco de Notas".',
        'Tecla Escape pressionada no "Bloco de Notas".',
        'A janela "Bloco de Notas" está aberta.',
        "O botão de Escape está visível.",
        "Fechei o Bloco de notas.",
    ):
        decision = guard.validate(response, tool_executed=False)
        assert decision.accepted is False


def test_calendar_claim_is_rejected_without_successful_tool() -> None:
    guard = ExecutionClaimGuard()

    for response in (
        "Sua agenda está livre.",
        "Não há eventos amanhã.",
        "Nenhum compromisso foi encontrado.",
        "Você está livre nesse horário.",
        "Esse período ficou ocupado.",
    ):
        decision = guard.validate(response, tool_executed=False)
        assert decision.accepted is False
        assert decision.reason == "result_claim_without_tool"


def test_calendar_write_claim_requires_matching_verified_tool() -> None:
    guard = ExecutionClaimGuard()

    for response in (
        "Marquei o dentista.",
        "Agendei a reunião.",
        "Criei o evento.",
        "Adicionei o compromisso à agenda.",
        "A reunião foi criada.",
    ):
        unrelated = guard.validate(
            response,
            tool_executed=True,
            successful_tool_names={"read_file"},
        )
        wrong_write = guard.validate(
            response,
            tool_executed=True,
            successful_tool_names={"google_calendar_update_event"},
        )
        verified = guard.validate(
            response,
            tool_executed=True,
            successful_tool_names={"google_calendar_create_event"},
        )
        assert unrelated.accepted is False
        assert unrelated.reason == "calendar_create_claim_without_verified_tool"
        assert wrong_write.accepted is False
        assert verified.accepted is True

    for response in (
        "Mudei o horário da reunião.",
        "Atualizei o evento.",
        "A reunião foi alterada.",
        "Alterei a data.",
        "O horário foi alterado.",
        "Remarquei para as 16h.",
    ):
        wrong_write = guard.validate(
            response,
            tool_executed=True,
            successful_tool_names={"google_calendar_create_event"},
        )
        verified = guard.validate(
            response,
            tool_executed=True,
            successful_tool_names={"google_calendar_update_event"},
        )
        assert wrong_write.accepted is False
        assert wrong_write.reason == "calendar_update_claim_without_verified_tool"
        assert verified.accepted is True

    for response in ("Cancelei o evento.", "Cancelei."):
        unsupported = guard.validate(
            response,
            tool_executed=True,
            successful_tool_names={"google_calendar_update_event"},
        )
        assert unsupported.accepted is False
        assert unsupported.reason == "unsupported_calendar_write_claim"
