from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.llm.ollama_client import OllamaLLM


class FakeOllamaClient:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    def chat(self, **kwargs: Any) -> SimpleNamespace:
        self.arguments = kwargs
        return SimpleNamespace(
            message=SimpleNamespace(
                content="Resposta",
                thinking="raciocínio que não deve ser usado",
                tool_calls=[],
            )
        )


def test_configured_keep_alive_is_sent_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeOllamaClient()
    monkeypatch.setattr(
        "jarvis.llm.ollama_client.Client",
        lambda **_kwargs: fake_client,
    )
    llm = OllamaLLM(model="test-model", keep_alive="45s")

    llm.generate(messages=[{"role": "user", "content": "Olá"}])

    assert fake_client.arguments["model"] == "test-model"
    assert fake_client.arguments["keep_alive"] == "45s"
    assert fake_client.arguments["think"] is False


def test_keep_alive_policy_can_be_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeOllamaClient()
    monkeypatch.setattr(
        "jarvis.llm.ollama_client.Client",
        lambda **_kwargs: fake_client,
    )
    llm = OllamaLLM()

    llm.set_keep_alive("30s")
    llm.generate(messages=[{"role": "user", "content": "Olá"}])

    assert fake_client.arguments["keep_alive"] == "30s"


def test_thinking_configuration_is_forwarded_and_separate_field_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeOllamaClient()
    monkeypatch.setattr(
        "jarvis.llm.ollama_client.Client",
        lambda **_kwargs: fake_client,
    )
    llm = OllamaLLM(thinking_enabled=False)
    response = llm.generate(messages=[])

    assert fake_client.arguments["think"] is False
    assert response.content == "Resposta"
