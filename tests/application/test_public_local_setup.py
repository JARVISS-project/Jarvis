import pytest

from tests.integration.test_administration_23bh import build


@pytest.mark.parametrize('model,expected', [
    ('qwen3:4b-instruct-2507-q4_K_M', 'ollama pull qwen3:4b-instruct-2507-q4_K_M'),
    ('bad; command', ''), ('bad\ncommand', ''), ('--help', ''),
])
def test_setup_instructions_are_local_and_never_generate_unsafe_shell_text(tmp_path, model, expected):
    h, service = build(tmp_path)
    try:
        service.config.model_name = model
        setup = service.first_use.inventory()['local_setup']
        assert setup['command'] == expected
        assert setup['download_url'] == 'https://ollama.com/download/windows'
        assert service.first_use.latest is None
    finally:
        h.close_confirmation_sessions()
