from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

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
            [SimpleNamespace(text=" olá "), SimpleNamespace(text="Jarvis")]
        )
        info = SimpleNamespace(language="pt", duration=1.25)
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
