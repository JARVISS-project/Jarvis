from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.llm.ollama_client import OllamaLLM


class FakeOllamaClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        content = (
            '{"answer":"ok"}'
            if "format" in kwargs
            else "ok"
        )
        return SimpleNamespace(
            message=SimpleNamespace(
                content=content,
                thinking="",
                tool_calls=[],
            )
        )


def test_context_window_is_sent_to_generate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeOllamaClient()
    monkeypatch.setattr(
        "jarvis.llm.ollama_client.Client",
        lambda **_kwargs: fake,
    )
    llm = OllamaLLM(context_window=8192)

    llm.generate([{"role": "user", "content": "oi"}])

    assert fake.calls[-1]["options"]["num_ctx"] == 8192


def test_context_window_is_sent_to_final_and_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeOllamaClient()
    monkeypatch.setattr(
        "jarvis.llm.ollama_client.Client",
        lambda **_kwargs: fake,
    )
    llm = OllamaLLM(context_window=8192)

    llm.generate_final([])
    llm.warm_up()

    assert fake.calls[-2]["options"]["num_ctx"] == 8192
    assert fake.calls[-1]["options"]["num_ctx"] == 8192
    assert fake.calls[-1]["options"]["num_predict"] == 4
