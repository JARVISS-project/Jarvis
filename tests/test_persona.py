from jarvis.persona import PersonaProfile


def test_jarvis_persona_requests_natural_tool_result_composition() -> None:
    prompt = PersonaProfile(address_mode="senhor").system_prompt()

    assert "assistente pessoal local" in prompt
    assert "dados brutos de uma Tool" in prompt
    assert "leia o conteúdo de modo fiel e natural" in prompt
    assert "várias ações explícitas" in prompt
    assert "'senhor' ocasionalmente" in prompt


def test_direct_persona_does_not_request_honorifics() -> None:
    prompt = PersonaProfile(address_mode="direto").system_prompt()
    assert "sem honoríficos" in prompt
