from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.llm.ollama_client import OllamaLLM


def test_calendar_advice_is_local_without_tools_or_conversation(monkeypatch):
    import json
    from io import BytesIO
    fake = FakeOllamaClient()
    fake.chat = lambda **kwargs: (setattr(fake, 'arguments', kwargs) or SimpleNamespace(message=SimpleNamespace(
        content='{"suggestion_ids": ["confirm"], "preparation_ids": ["questions"]}', tool_calls=[])))
    monkeypatch.setattr('jarvis.llm.ollama_client.Client', lambda **kwargs: fake)
    metadata = {'models': [{'name': 'test-model:latest', 'size': 2000000, 'digest': 'a' * 64}]}
    urls = []
    class Opener:
        def open(self, url, timeout):
            urls.append(url)
            return BytesIO(json.dumps(metadata).encode())
    monkeypatch.setattr('jarvis.llm.ollama_client.build_opener', lambda *args: Opener())
    llm = OllamaLLM(model='test-model')
    answer = llm.calendar_suggestions({'title': 'Encontro fictício'})
    assert answer['suggestion_ids'] == ['confirm']
    assert urls == ['http://localhost:11434/api/tags']
    assert fake.arguments['tools'] == [] and fake.arguments['think'] is False
    assert fake.arguments['format']['additionalProperties'] is False
    fake.arguments = {}
    metadata['models'][0]['remote_host'] = 'https://ollama.com'
    with pytest.raises(PermissionError):
        llm.calendar_suggestions({'title': 'Evento privado'})
    assert fake.arguments == {}  # No prompt reaches a remote-backed model.


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


def test_structured_final_retry_also_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeOllamaClient()

    def structured_chat(**kwargs: Any) -> SimpleNamespace:
        fake_client.arguments = kwargs
        return SimpleNamespace(
            message=SimpleNamespace(
                content='{"answer":"Resposta final segura."}',
                thinking="segredo",
                tool_calls=[],
            )
        )

    fake_client.chat = structured_chat  # type: ignore[method-assign]
    monkeypatch.setattr(
        "jarvis.llm.ollama_client.Client", lambda **_kwargs: fake_client
    )
    response = OllamaLLM(thinking_enabled=True).generate_final([])
    assert fake_client.arguments["think"] is False
    assert fake_client.arguments["tools"] == []
    assert fake_client.arguments["format"]["required"] == ["answer"]
    assert response.content == "Resposta final segura."


def test_warm_up_loads_model_without_tools_or_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeOllamaClient()
    monkeypatch.setattr(
        "jarvis.llm.ollama_client.Client",
        lambda **_kwargs: fake_client,
    )
    llm = OllamaLLM(model="test-model", keep_alive="5m")

    llm.warm_up()

    assert fake_client.arguments["model"] == "test-model"
    assert fake_client.arguments["tools"] == []
    assert fake_client.arguments["think"] is False
    assert fake_client.arguments["options"]["num_predict"] == 4
