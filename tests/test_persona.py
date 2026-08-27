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


def test_voice_response_mode_requests_short_spoken_answers() -> None:
    prompt = PersonaProfile().system_prompt(response_mode="voice")

    assert "uma ou duas frases curtas" in prompt
    assert "45 palavras" in prompt
    assert "leitura literal" in prompt
