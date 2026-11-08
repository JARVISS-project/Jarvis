from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.runtime.resource_manager import ResourceManager
from jarvis.voice.pipeline import VoicePipeline
from jarvis.voice.semantic_shadow import (
    SemanticShadowObserver,
    load_catalog,
    resolve_text,
    sanitize_text,
)
from tests.voice.test_pipeline import FakeHarness, FakeListener, FakeRecorder, FakeSpeechToText


def _pipeline(**kwargs) -> VoicePipeline:
    return VoicePipeline(
        listener=FakeListener([]),  # type: ignore[arg-type]
        recorder=FakeRecorder([1]),  # type: ignore[arg-type]
        speech_to_text=FakeSpeechToText(),
        harness=kwargs.pop("harness", FakeHarness("unchanged")),
        resource_manager=ResourceManager(),
        output=lambda _line: None,
        **kwargs,
    )


def test_disabled_flag_does_not_instantiate_or_call() -> None:
    calls = []
    pipeline = _pipeline(
        semantic_shadow_enabled=False,
        semantic_shadow_factory=lambda: calls.append("created"),  # type: ignore[arg-type]
    )
    pipeline._process_text("Que horas são?")
    assert calls == []


def test_enabled_shadow_only_observes_and_preserves_response() -> None:
    class Observer:
        def __init__(self) -> None:
            self.calls = []

        def observe(self, text: str, **fields) -> None:
            self.calls.append((text, fields))

        def close(self) -> None:
            pass

    observer = Observer()
    harness = FakeHarness("exact response")
    pipeline = _pipeline(
        harness=harness,
        semantic_shadow_enabled=True,
        semantic_shadow_factory=lambda: observer,  # type: ignore[arg-type]
    )
    before = pipeline.resource_manager.state
    result = pipeline._process_text("Abra o notepad")
    assert result == (True, False)
    assert harness.calls == [("Abra o notepad", False)]
    assert observer.calls[0][0] == "Abra o notepad"
    assert pipeline.resource_manager.state is before


def test_shadow_exception_does_not_affect_pipeline() -> None:
    class BrokenObserver:
        def observe(self, *_args, **_kwargs) -> None:
            raise RuntimeError("private payload")

        def close(self) -> None:
            pass

    harness = FakeHarness("same response")
    pipeline = _pipeline(
        harness=harness,
        semantic_shadow_enabled=True,
        semantic_shadow_factory=BrokenObserver,  # type: ignore[arg-type]
    )
    assert pipeline._process_text("texto") == (True, False)
    assert harness.calls == [("texto", False)]


def test_resolver_has_no_tool_or_confirmation_dependency() -> None:
    catalog = load_catalog()
    assert resolve_text("Que horas são?", catalog).decision == "execute"
    assert resolve_text("Não abra o notepad", catalog).decision == "reject"
    assert resolve_text("comando inexistente", catalog).decision == "reject"
    assert "tool" not in SemanticShadowObserver.__init__.__code__.co_varnames
    assert "confirmation" not in SemanticShadowObserver.__init__.__code__.co_varnames


def test_sensitive_text_is_sanitized_in_jsonl(tmp_path: Path) -> None:
    target = tmp_path / "shadow.jsonl"
    observer = SemanticShadowObserver(log_path=target)
    observer.observe(
        "Minha senha: secreta token=abc123 email eu@example.com CPF 12345678901",
        activation_origin="wake_word",
    )
    observer.close()
    content = target.read_text(encoding="utf-8")
    assert "secreta" not in content
    assert "abc123" not in content
    assert "eu@example.com" not in content
    assert "12345678901" not in content
    assert "[redacted]" in content
    row = json.loads(content)
    assert set(row) == {
        "interaction_id", "activation_origin", "normalized_text", "candidate",
        "decision", "reason", "duration_ms", "catalog_version", "error",
    }


def test_log_rotation_is_bounded(tmp_path: Path) -> None:
    target = tmp_path / "shadow.jsonl"
    observer = SemanticShadowObserver(log_path=target, max_bytes=300, backup_count=1)
    for index in range(20):
        observer.observe(f"comando inexistente {index}", activation_origin="ptt")
    observer.close()
    assert target.exists()
    assert (tmp_path / "shadow.jsonl.1").exists()
    assert len(list(tmp_path.glob("shadow.jsonl*"))) == 2


def test_missing_or_invalid_catalog_fails_closed(tmp_path: Path) -> None:
    for path in (tmp_path / "missing.json", tmp_path / "invalid.json"):
        if path.name == "invalid.json":
            path.write_text("{}", encoding="utf-8")
        target = tmp_path / f"{path.stem}.jsonl"
        observer = SemanticShadowObserver(catalog_path=path, log_path=target)
        result = observer.observe("Que horas são?", activation_origin="wake_word")
        observer.close()
        row = json.loads(target.read_text(encoding="utf-8"))
        assert result.decision == "reject"
        assert result.risk == "unknown"
        assert row["error"] in {"FileNotFoundError", "ValueError"}
        assert "missing.json" not in row["error"]


@pytest.mark.parametrize("text", [
    "Abra o notepad", "Feche o notepad", "Não abra o notepad",
    "Abra o notepad e feche o calculator", "Abra C:\\Windows\\System32\\cmd.exe",
    "Ligue as luzes da sala", "Desligue as luzes da sala",
    "Defina um timer de cinco minutos", "comando inexistente",
])
def test_decisions_are_deterministic_and_unknown_never_executes(text: str) -> None:
    catalog = load_catalog()
    decisions = [resolve_text(text, catalog) for _ in range(5)]
    assert len({(row.decision, row.reason, row.candidate, row.risk) for row in decisions}) == 1
    if decisions[0].risk == "unknown":
        assert decisions[0].decision == "reject"


def test_sanitizer_normalizes_without_credentials() -> None:
    assert sanitize_text("  QUE horas SÃO?! ") == "que horas são"
    assert sanitize_text("Bearer token: abc") == "bearer [redacted]"
