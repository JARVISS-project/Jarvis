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
        state_callback: Any = None,
        wake_word_threshold: float | None = None,
        desktop_kill_switch: Any = None,
    ) -> None:
        assert state_callback is None
        assert wake_word_threshold is None
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
        "build_text_to_speech",
        lambda _config: (_ for _ in ()).throw(
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


def test_incomplete_turn_wait_does_not_limit_continuation_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDetector:
        frame_length = 1280
        sample_rate = 16000

        def close(self) -> None:
            pass

    class RecorderProbe:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    captured_pipeline: dict[str, Any] = {}

    class FakePipeline:
        def __init__(self, **kwargs: Any) -> None:
            captured_pipeline.update(kwargs)

        def run(self) -> None:
            pass

    monkeypatch.setattr(main_module, "build_harness", lambda *_args: object())
    monkeypatch.setattr(
        main_module,
        "OpenWakeWordDetector",
        lambda **_kwargs: FakeDetector(),
    )
    monkeypatch.setattr(main_module, "PvRecorderAudioInput", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "WakeWordListener", lambda *_args: object())
    monkeypatch.setattr(main_module, "VoiceAudioSession", lambda _listener: object())
    monkeypatch.setattr(main_module, "UtteranceRecorder", RecorderProbe)
    monkeypatch.setattr(main_module, "VoicePipeline", FakePipeline)

    config = JarvisConfig(
        voice_incomplete_turn_extension_seconds=2.0,
        voice_max_recording_seconds=15.0,
    )
    main_module.run_voice_mode(config)

    recorder = captured_pipeline["incomplete_turn_recorder"]
    assert isinstance(recorder, RecorderProbe)
    assert recorder.kwargs["pre_speech_timeout_seconds"] == 2.0
    assert recorder.kwargs["max_recording_seconds"] == 15.0


def test_voice_mode_forwards_application_state_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDetector:
        frame_length = 1280
        sample_rate = 16000

        def close(self) -> None:
            pass

    captured_pipeline: dict[str, Any] = {}

    class FakePipeline:
        def __init__(self, **kwargs: Any) -> None:
            captured_pipeline.update(kwargs)

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

    callback = lambda _state: None
    config = JarvisConfig()
    main_module.run_voice_mode(
        config,
        state_callback=callback,
        wake_word_threshold=0.44,
    )

    assert captured_pipeline["state_callback"] is callback
    assert captured_pipeline["wake_word_label"] == "Jarvis"


def test_voice_threshold_override_is_forwarded_to_detector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector_arguments: dict[str, Any] = {}

    class FakeDetector:
        frame_length = 1280
        sample_rate = 16000

        def close(self) -> None:
            pass

    class FakePipeline:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def run(self) -> None:
            pass

    def fake_detector(**kwargs: Any) -> FakeDetector:
        detector_arguments.update(kwargs)
        return FakeDetector()

    monkeypatch.setattr(main_module, "build_harness", lambda *_args: object())
    monkeypatch.setattr(main_module, "OpenWakeWordDetector", fake_detector)
    monkeypatch.setattr(main_module, "PvRecorderAudioInput", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "WakeWordListener", lambda *_args: object())
    monkeypatch.setattr(main_module, "VoiceAudioSession", lambda _listener: object())
    monkeypatch.setattr(main_module, "UtteranceRecorder", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "VoicePipeline", FakePipeline)

    main_module.run_voice_mode(JarvisConfig(), wake_word_threshold=0.43)

    assert detector_arguments["threshold"] == 0.43


def test_ui_flag_starts_live_state_channel_without_coupling_ui_to_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, Any] = {}
    process = object()

    class FakeStateServer:
        def __init__(self, _state_bus: Any, *, endpoint_path: Path) -> None:
            captured["endpoint_path"] = endpoint_path

        def start(self) -> None:
            events.append("server-start")

        def close(self) -> None:
            events.append("server-close")

    def fake_launch(endpoint_path: Path) -> object:
        captured["launch_path"] = endpoint_path
        events.append("ui-launch")
        return process

    def fake_stop(value: object) -> None:
        assert value is process
        events.append("ui-stop")

    def fake_voice_mode(**kwargs: Any) -> None:
        captured.update(kwargs)
        events.append("voice-run")
        assert callable(kwargs["state_callback"])

    monkeypatch.setattr(main_module, "LocalStateServer", FakeStateServer)
    monkeypatch.setattr(main_module, "launch_ambient_ui", fake_launch)
    monkeypatch.setattr(main_module, "stop_spawned_ambient_ui", fake_stop)
    monkeypatch.setattr(main_module, "run_voice_mode", fake_voice_mode)

    main_module.main(["--ui"])

    assert captured["endpoint_path"] == Path("data/runtime/ambient-ui.json")
    assert captured["launch_path"] == Path("data/runtime/ambient-ui.json")
    assert events == [
        "server-start",
        "ui-launch",
        "voice-run",
        "ui-stop",
        "server-close",
    ]


def test_invalid_wake_word_threshold_fails_before_voice_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "run_voice_mode",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Voice Runtime não deve iniciar")
        ),
    )

    with pytest.raises(SystemExit, match="wake-word-threshold"):
        main_module.main(["--voice", "--wake-word-threshold", "1.2"])


def test_tts_pace_cli_override_is_session_only_and_reaches_voice_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_voice_mode(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(main_module, "run_voice_mode", fake_voice_mode)

    main_module.main(["--voice", "--tts-pace", "natural"])

    assert captured["config"].tts_piper_pace == "natural"
    assert captured["config"].tts_piper_length_scale == 1.18
