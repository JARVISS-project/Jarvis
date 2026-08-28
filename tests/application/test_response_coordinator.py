from types import SimpleNamespace

from jarvis.application.response_coordinator import ResponseCoordinator


def test_advisory_finalization_does_not_retry_rejected_response() -> None:
    llm = SimpleNamespace(
        generate_final=lambda _messages: (_ for _ in ()).throw(
            AssertionError("não deve fazer segunda chamada")
        )
    )
    rejected = SimpleNamespace(accepted=False, text="", reason="teste")
    coordinator = ResponseCoordinator(
        config=SimpleNamespace(llm_debug_enabled=False),
        context_router=SimpleNamespace(),
        recent_context=SimpleNamespace(),
        memory_store=SimpleNamespace(),
        tool_registry=SimpleNamespace(),
        llm=llm,
        final_response_firewall=SimpleNamespace(
            validate=lambda _content: rejected
        ),
        execution_claim_guard=SimpleNamespace(),
        confirmation_interpreter=SimpleNamespace(),
        obsidian_write_available=lambda: False,
        latency_provider=lambda: {"llm_seconds": 0.0, "llm_calls": 1},
        safe_fallback_response="fallback seguro",
    )

    result = coordinator.finalize_llm_response(
        "resposta rejeitada",
        [],
        allow_retry=False,
    )

    assert result == "fallback seguro"
