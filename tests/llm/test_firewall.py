import pytest

from jarvis.llm.firewall import FinalResponseFirewall


@pytest.mark.parametrize(
    "leak",
    [
        "Okay, the user asked for the files.",
        "Wait, no... I should use another tool.",
        "Looking at the tools, I need to respond.",
        "The assistant should ask the user.",
        '<tool_call>{"name":"read_file","arguments":{}}</tool_call>',
        '{"name":"read_file","arguments":{"path":"README.md"}}',
    ],
)
def test_reasoning_and_tool_protocol_are_blocked(leak: str) -> None:
    result = FinalResponseFirewall().validate(leak)
    assert result.accepted is False
    assert result.text == ""


def test_tagged_reasoning_can_only_expose_clear_final_suffix() -> None:
    result = FinalResponseFirewall().validate(
        "<think>Wait, no. Internal.</think>Resposta correta."
    )
    assert result.accepted is True
    assert result.text == "Resposta correta."


def test_normal_portuguese_response_is_accepted() -> None:
    result = FinalResponseFirewall().validate(
        "Encontrei duas notas sobre o projeto."
    )
    assert result.accepted is True


def test_firewall_stress_has_no_reasoning_leaks() -> None:
    firewall = FinalResponseFirewall()
    leaks = ["Okay, the user asked... Wait, no..."] * 100
    normal = [f"Resposta normal {index}." for index in range(100)]
    assert all(not firewall.validate(item).accepted for item in leaks)
    assert sum(firewall.validate(item).accepted for item in normal) == 100
