from pathlib import Path
from typing import Any

import pytest

from jarvis import __main__ as main_module
from jarvis.config import JarvisConfig
from jarvis.runtime import cli as runtime_cli
from jarvis.speech.gpu_budget import GpuBudgetError, GpuBudgetManager, GpuMemory
from jarvis.speech.stt_process import SttProcessClient


def test_voice_flag_selects_managed_runtime_without_constructing_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path | None, int | None]] = []

    def fail_if_constructed(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Componente pesado não deve ser criado")

    def fake_runtime(args: Any, _config: Any) -> None:
        calls.append((args.wake_word_model_path, args.microphone_device_index))

    monkeypatch.setattr(main_module, "OllamaLLM", fail_if_constructed)
    monkeypatch.setattr(main_module, "ToolExecutor", fail_if_constructed)
    monkeypatch.setattr(runtime_cli, "_run_managed_runtime", fake_runtime)

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

        def close(self) -> None:
            pass

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
    capsys: pytest.CaptureFixture[str],
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
            assert kwargs["stt_candidate_shadow"] is None

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
    monkeypatch.setenv("JARVIS_STT_CANDIDATE_SHADOW", "true")
    monkeypatch.setenv("JARVIS_STT_CANDIDATE_MODEL", "large-v3")
    monkeypatch.setenv("JARVIS_STT_PROFILE", "large-v3")
    monkeypatch.setenv("JARVIS_STT_EXECUTION_MODE", "process")
    monkeypatch.setattr(
        main_module, "query_gpu_memory",
        lambda: GpuMemory(12288, 2000, 10288),
    )

    main_module.run_voice_mode(JarvisConfig())

    assert len(captured_stt) == 1
    assert captured_stt[0].is_model_loaded is False
    assert "reason=incompatible_primary" in capsys.readouterr().out
    assert isinstance(captured_stt[0], SttProcessClient)
    assert captured_stt[0].config.device == "cuda"


def test_failed_budget_never_constructs_or_loads_primary_stt(monkeypatch) -> None:
    class FakeDetector:
        frame_length = 1280
        sample_rate = 16000
        def close(self) -> None: pass

    monkeypatch.setattr(main_module, "build_harness", lambda *_args: object())
    monkeypatch.setattr(main_module, "OpenWakeWordDetector",
                        lambda **_kwargs: FakeDetector())
    monkeypatch.setattr(main_module, "PvRecorderAudioInput",
                        lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "WakeWordListener", lambda *_args: object())
    monkeypatch.setattr(main_module, "VoiceAudioSession", lambda _listener: object())
    monkeypatch.setattr(main_module, "UtteranceRecorder", lambda **_kwargs: object())
    monkeypatch.setattr(
        main_module, "SttProcessClient",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("STT must not be constructed without budget")
        ),
    )
    config = JarvisConfig(stt_profile="large-v3", stt_execution_mode="process")
    with pytest.raises(GpuBudgetError) as caught:
        main_module.run_voice_mode(
            config,
            gpu_budget=GpuBudgetManager(lambda: GpuMemory(12288, 7000, 5288)),
        )
    assert caught.value.decision.status == "insufficient"


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
    main_module.run_voice_mode(
        config,
        gpu_budget=GpuBudgetManager(lambda: GpuMemory(12288, 2000, 10288)),
    )

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
        gpu_budget=GpuBudgetManager(lambda: GpuMemory(12288, 2000, 10288)),
    )

    assert captured_pipeline["state_callback"] is callback
    assert captured_pipeline["wake_word_label"] == "Hey Jarvis"


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

    main_module.run_voice_mode(
        JarvisConfig(), wake_word_threshold=0.43,
        gpu_budget=GpuBudgetManager(lambda: GpuMemory(12288, 2000, 10288)),
    )

    assert detector_arguments["threshold"] == 0.43


def test_ui_flag_is_independent_from_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_runtime(args: Any, _config: Any) -> None:
        captured.update(vars(args))

    monkeypatch.setattr(runtime_cli, "_run_managed_runtime", fake_runtime)

    main_module.main(["--ui"])

    assert captured["ui"] is True
    assert captured["voice"] is False
    assert captured["remote"] is False


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

    def fake_runtime(_args: Any, config: JarvisConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(runtime_cli, "_run_managed_runtime", fake_runtime)

    main_module.main(["--voice", "--tts-pace", "natural"])

    assert captured["config"].tts_piper_pace == "natural"
    assert captured["config"].tts_piper_length_scale == 1.18


def test_voice_verifier_is_forwarded_only_when_enabled_and_file_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector_calls: list[dict[str, Any]] = []

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
        detector_calls.append(dict(kwargs))
        return FakeDetector()

    monkeypatch.setattr(main_module, "build_harness", lambda *_args: object())
    monkeypatch.setattr(main_module, "OpenWakeWordDetector", fake_detector)
    monkeypatch.setattr(
        main_module, "PvRecorderAudioInput", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        main_module, "WakeWordListener", lambda *_args: object()
    )
    monkeypatch.setattr(
        main_module, "VoiceAudioSession", lambda _listener: object()
    )
    monkeypatch.setattr(
        main_module, "UtteranceRecorder", lambda **_kwargs: object()
    )
    monkeypatch.setattr(main_module, "VoicePipeline", FakePipeline)

    verifier_path = tmp_path / "hey_jarvis_verifier.npz"
    config = JarvisConfig(wake_word_verifier_path=verifier_path)

    main_module.run_voice_mode(
        config,
        gpu_budget=GpuBudgetManager(lambda: GpuMemory(12288, 2000, 10288)),
    )
    assert detector_calls[-1]["verifier_path"] is None

    verifier_path.write_bytes(b"local-test-artifact")
    main_module.run_voice_mode(
        config,
        gpu_budget=GpuBudgetManager(lambda: GpuMemory(12288, 2000, 10288)),
    )
    assert detector_calls[-1]["verifier_path"] is None
    assert detector_calls[-1]["threshold"] == 0.5
    assert detector_calls[-1]["minimum_consecutive_detections"] == 2

    config.wake_word_verifier_enabled = True
    main_module.run_voice_mode(
        config,
        gpu_budget=GpuBudgetManager(lambda: GpuMemory(12288, 2000, 10288)),
    )

    assert detector_calls[-1]["verifier_path"] == verifier_path
    assert detector_calls[-1]["verifier_candidate_threshold"] == 0.12
    assert detector_calls[-1]["verifier_threshold"] == 0.74
    assert detector_calls[-1]["verifier_strong_threshold"] == 0.85
