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
