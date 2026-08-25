from pathlib import Path
from typing import Any

import pytest

from jarvis import __main__ as main_module
from jarvis.config import JarvisConfig


def test_voice_mode_does_not_construct_llm_or_tool_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path | None, int | None]] = []

    def fail_if_constructed(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Componente pesado não deve ser criado")

    def fake_voice_mode(
        config: Any,
        model_path: Path | None,
        microphone_device_index: int | None,
    ) -> None:
        calls.append((model_path, microphone_device_index))

    monkeypatch.setattr(main_module, "OllamaLLM", fail_if_constructed)
    monkeypatch.setattr(main_module, "ToolExecutor", fail_if_constructed)
    monkeypatch.setattr(main_module, "run_voice_mode", fake_voice_mode)

    main_module.main(
        [
            "--voice",
            "--microphone-device-index",
            "3",
        ]
    )

    assert calls == [(None, 3)]


def test_traditional_cli_still_starts_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness_runs: list[bool] = []

    class FakeHarness:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def run(self) -> None:
            harness_runs.append(True)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module,
        "OllamaLLM",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(main_module, "Harness", FakeHarness)
    monkeypatch.setattr(
        main_module,
        "WindowsSapiTTS",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("CLI tradicional não deve inicializar TTS")
        ),
    )

    main_module.main([])

    assert harness_runs == [True]


def test_starting_voice_mode_does_not_load_stt_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_stt: list[Any] = []

    class FakeDetector:
        frame_length = 1280
        sample_rate = 16000

        def close(self) -> None:
            pass

    class FakePipeline:
        def __init__(self, **kwargs: Any) -> None:
            captured_stt.append(kwargs["speech_to_text"])

        def run(self) -> None:
            pass

    monkeypatch.setattr(main_module, "build_harness", lambda *_args: object())
    monkeypatch.setattr(
        main_module,
        "OpenWakeWordDetector",
        lambda **_kwargs: FakeDetector(),
    )
    monkeypatch.setattr(
        main_module,
        "PvRecorderAudioInput",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        main_module,
        "WakeWordListener",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        main_module,
        "VoiceAudioSession",
        lambda _listener: object(),
    )
    monkeypatch.setattr(
        main_module,
        "UtteranceRecorder",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(main_module, "VoicePipeline", FakePipeline)

    main_module.run_voice_mode(JarvisConfig())

    assert len(captured_stt) == 1
    assert captured_stt[0].is_model_loaded is False
