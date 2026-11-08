from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.speech.faster_whisper import FasterWhisperSTT


class FakeWhisperModel:
    def __init__(self) -> None:
        self.audio: Any = None
        self.arguments: dict[str, Any] = {}

    def transcribe(
        self,
        audio: Any,
        **kwargs: Any,
    ) -> tuple[Any, Any]:
        self.audio = audio
        self.arguments = kwargs
        segments = iter(
            [
                SimpleNamespace(
                    text=" olá ", avg_logprob=-0.2, no_speech_prob=0.1
                ),
                SimpleNamespace(
                    text="Jarvis", avg_logprob=-0.4, no_speech_prob=0.2
                ),
            ]
        )
        info = SimpleNamespace(
            language="pt",
            duration=1.25,
            language_probability=0.98,
            duration_after_vad=0.9,
        )
        return segments, info


def test_stt_model_loading_is_lazy() -> None:
    factory_calls: list[tuple[Any, ...]] = []

    def factory(*args: Any, **kwargs: Any) -> FakeWhisperModel:
        factory_calls.append((args, kwargs))
        return FakeWhisperModel()

    stt = FasterWhisperSTT(model_factory=factory)

    assert stt.is_model_loaded is False
    assert factory_calls == []


def test_transcribe_loads_cpu_int8_model_and_returns_result() -> None:
    factory_arguments: dict[str, Any] = {}
    model = FakeWhisperModel()

    def factory(model_name: str, **kwargs: Any) -> FakeWhisperModel:
        factory_arguments["model_name"] = model_name
        factory_arguments.update(kwargs)
        return model

    converted_audio: list[Sequence[int]] = []
    stt = FasterWhisperSTT(
        model_name="small",
        model_factory=factory,
        audio_converter=lambda audio: converted_audio.append(audio) or audio,
    )

    result = stt.transcribe([100, -100], sample_rate=16000)

    assert stt.is_model_loaded is True
    assert factory_arguments == {
        "model_name": "small",
        "device": "cpu",
        "compute_type": "int8",
    }
    assert converted_audio == [[100, -100]]
    assert model.audio == [100, -100]
    assert result.text == "olá Jarvis"
    assert result.language == "pt"
    assert result.duration_seconds == 1.25
    assert result.average_log_probability == pytest.approx(-0.3)
    assert result.no_speech_probability == 0.2
    assert result.language_probability == 0.98
    assert result.duration_after_vad_seconds == 0.9
    assert model.arguments["condition_on_previous_text"] is False
    assert "initial_prompt" not in model.arguments
    assert model.arguments["vad_filter"] is True
    assert model.arguments["vad_parameters"] == {
        "min_silence_duration_ms": 500,
        "speech_pad_ms": 200,
        "threshold": 0.5,
    }


def test_runtime_options_prepare_game_mode_profile() -> None:
    arguments: dict[str, Any] = {}

    def factory(_model_name: str, **kwargs: Any) -> FakeWhisperModel:
        arguments.update(kwargs)
        return FakeWhisperModel()

    stt = FasterWhisperSTT(
        runtime_options=lambda: ("cpu", "int8"),
        model_factory=factory,
        audio_converter=lambda audio: audio,
    )

    stt.transcribe([1], sample_rate=16000)

    assert arguments == {"device": "cpu", "compute_type": "int8"}


def test_close_unloads_model() -> None:
    stt = FasterWhisperSTT(
        model_factory=lambda *_args, **_kwargs: FakeWhisperModel(),
        audio_converter=lambda audio: audio,
    )
    stt.transcribe([1], sample_rate=16000)

    stt.close()

    assert stt.is_model_loaded is False


def test_configured_language_reaches_faster_whisper() -> None:
    model = FakeWhisperModel()
    stt = FasterWhisperSTT(
        language="pt",
        model_factory=lambda *_args, **_kwargs: model,
        audio_converter=lambda audio: audio,
    )

    stt.transcribe([1], sample_rate=16000)

    assert model.arguments["language"] == "pt"
    assert model.arguments["beam_size"] == 1


def test_short_initial_prompt_reaches_faster_whisper() -> None:
    model = FakeWhisperModel()
    stt = FasterWhisperSTT(
        initial_prompt="README.md, JSON, underscore",
        model_factory=lambda *_args, **_kwargs: model,
        audio_converter=lambda audio: audio,
    )
    stt.transcribe([1], sample_rate=16000)
    assert model.arguments["initial_prompt"] == "README.md, JSON, underscore"


def test_dynamic_hotwords_are_built_only_when_transcribing() -> None:
    model = FakeWhisperModel()
    values = ["Jarvis, README.md", "Jarvis, VLSM.md"]
    calls = 0

    def hotwords() -> str:
        nonlocal calls
        value = values[calls]
        calls += 1
        return value

    stt = FasterWhisperSTT(
        hotwords_provider=hotwords,
        model_factory=lambda *_args, **_kwargs: model,
        audio_converter=lambda audio: audio,
    )
    assert calls == 0
    stt.transcribe([1], 16000)
    assert model.arguments["hotwords"] == "Jarvis, README.md"
    assert calls == 1


def test_vad_and_hotwords_can_be_disabled_for_future_backends() -> None:
    model = FakeWhisperModel()
    stt = FasterWhisperSTT(
        vad_enabled=False,
        hotwords_enabled=False,
        hotwords_provider=lambda: "não deve ser usado",
        model_factory=lambda *_args, **_kwargs: model,
        audio_converter=lambda audio: audio,
    )
    stt.transcribe([1], 16000)
    assert model.arguments["vad_filter"] is False
    assert "vad_parameters" not in model.arguments
    assert "hotwords" not in model.arguments


def test_retry_uses_more_precise_beam_on_same_audio() -> None:
    model = FakeWhisperModel()
    converted: list[Sequence[int]] = []
    stt = FasterWhisperSTT(
        model_factory=lambda *_args, **_kwargs: model,
        audio_converter=lambda audio: converted.append(audio) or audio,
    )
    audio = [1, 2, 3]
    stt.transcribe_retry(audio, 16000)
    assert converted == [audio]
    assert model.arguments["beam_size"] == 5


def test_auto_device_prefers_cuda_when_ctranslate2_reports_it() -> None:
    arguments: dict[str, Any] = {}

    def factory(model_name: str, **kwargs: Any) -> FakeWhisperModel:
        arguments.update(model=model_name, **kwargs)
        return FakeWhisperModel()

    stt = FasterWhisperSTT(
        model_name="medium",
        device="auto",
        compute_type="auto",
        cuda_available=lambda: True,
        model_factory=factory,
        audio_converter=lambda audio: audio,
    )
    assert not stt.is_model_loaded
    stt.transcribe([1], 16000)
    assert arguments == {
        "model": "medium", "device": "cuda", "compute_type": "float16"
    }


def test_cuda_initialization_failure_falls_back_once_to_small_cpu() -> None:
    calls: list[tuple[str, str, str]] = []
    output: list[str] = []

    def factory(model_name: str, **kwargs: Any) -> FakeWhisperModel:
        calls.append((model_name, kwargs["device"], kwargs["compute_type"]))
        if kwargs["device"] == "cuda":
            raise RuntimeError("cuDNN indisponível")
        return FakeWhisperModel()

    stt = FasterWhisperSTT(
        model_name="medium",
        device="auto",
        compute_type="auto",
        cuda_available=lambda: True,
        model_factory=factory,
        audio_converter=lambda audio: audio,
        output=output.append,
    )
    stt.transcribe([1], 16000)
    stt.transcribe([1], 16000)
    assert calls == [
        ("medium", "cuda", "float16"),
        ("small", "cpu", "int8"),
    ]
    assert output == ["[stt] CUDA indisponível; usando fallback CPU."]


def test_candidate_can_disable_device_fallback() -> None:
    calls: list[tuple[str, str, str]] = []

    def factory(model_name: str, **kwargs: Any) -> FakeWhisperModel:
        calls.append((model_name, kwargs["device"], kwargs["compute_type"]))
        raise MemoryError("candidate GPU exhausted")

    stt = FasterWhisperSTT(
        model_name="large-v3",
        device="cuda",
        compute_type="float16",
        allow_device_fallback=False,
        model_factory=factory,
        audio_converter=lambda audio: audio,
    )
    with pytest.raises(MemoryError, match="exhausted"):
        stt.transcribe([1], 16000)
    assert calls == [("large-v3", "cuda", "float16")]


def test_runtime_model_profile_switches_normal_and_game_mode() -> None:
    current = ["normal"]
    calls: list[tuple[str, str, str]] = []

    def options() -> tuple[str, str, str]:
        return (
            ("medium", "auto", "auto")
            if current[0] == "normal"
            else ("small", "cpu", "int8")
        )

    def factory(model_name: str, **kwargs: Any) -> FakeWhisperModel:
        calls.append((model_name, kwargs["device"], kwargs["compute_type"]))
        return FakeWhisperModel()

    stt = FasterWhisperSTT(
        runtime_options=options,
        cuda_available=lambda: True,
        model_factory=factory,
        audio_converter=lambda audio: audio,
    )
    stt.transcribe([1], 16000)
    current[0] = "game"
    stt.transcribe([1], 16000)
    assert calls == [
        ("medium", "cuda", "float16"),
        ("small", "cpu", "int8"),
    ]


def test_warm_up_loads_and_verifies_backend_with_synthetic_pcm() -> None:
    created: list[FakeWhisperModel] = []

    def factory(*_args: object, **_kwargs: object) -> FakeWhisperModel:
        model = FakeWhisperModel()
        created.append(model)
        return model

    stt = FasterWhisperSTT(
        model_name="medium",
        device="cpu",
        compute_type="int8",
        model_factory=factory,
    )

    stt.warm_up()

    assert stt.is_model_loaded is True
    assert len(created) == 1
    assert len(created[0].audio) == 1600
