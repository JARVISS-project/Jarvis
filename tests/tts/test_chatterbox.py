from pathlib import Path
from typing import Any

from jarvis.tts.base import TextToSpeech
from jarvis.tts.chatterbox import ChatterboxTTS


class FakeModel:
    sr = 24000

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def generate(self, text: str, **kwargs: Any) -> list[float]:
        self.calls.append((text, kwargs))
        return [0.0, 0.1, -0.1]


class FakePlayer:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, int]] = []
        self.stops = 0

    def play(self, audio: Any, sample_rate: int) -> None:
        self.calls.append((audio, sample_rate))

    def stop(self) -> None:
        self.stops += 1


class RecordingFallback(TextToSpeech):
    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.stops = 0

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        self.stops += 1


def test_chatterbox_is_lazy_and_generates_portuguese_with_reference() -> None:
    model = FakeModel()
    player = FakePlayer()
    factory_calls: list[tuple[str, str]] = []

    def factory(device: str, t3_model: str) -> FakeModel:
        factory_calls.append((device, t3_model))
        return model

    tts = ChatterboxTTS(
        device="cuda",
        language_id="pt",
        reference_audio_path=Path("voz-original.wav"),
        t3_model="v3",
        exaggeration=0.35,
        cfg_weight=0.30,
        model_factory=factory,
        audio_player=player,
    )

    assert tts.is_model_loaded is False
    tts.speak("Pronto, senhor.")

    assert tts.is_model_loaded is True
    assert factory_calls == [("cuda", "v3")]
    assert model.calls == [
        (
            "Pronto, senhor.",
            {
                "language_id": "pt",
                "exaggeration": 0.35,
                "cfg_weight": 0.30,
                "audio_prompt_path": "voz-original.wav",
            },
        )
    ]
    assert player.calls == [([0.0, 0.1, -0.1], 24000)]
    tts.close()


def test_chatterbox_falls_back_and_disables_failed_primary() -> None:
    fallback = RecordingFallback()
    player = FakePlayer()
    calls = 0

    def broken_factory(_device: str, _t3_model: str) -> FakeModel:
        nonlocal calls
        calls += 1
        raise RuntimeError("CUDA indisponível")

    tts = ChatterboxTTS(
        model_factory=broken_factory,
        audio_player=player,
        fallback=fallback,
    )

    tts.speak("Primeira resposta")
    tts.speak("Segunda resposta")

    assert calls == 1
    assert fallback.spoken == ["Primeira resposta", "Segunda resposta"]
    assert isinstance(tts.last_primary_error, RuntimeError)
    tts.close()


def test_default_model_factory_passes_t3_model_when_supported(monkeypatch) -> None:
    import sys
    import types

    calls: list[tuple[str, str]] = []
    model = FakeModel()

    class NewApiModel:
        @classmethod
        def from_pretrained(cls, *, device: str, t3_model: str) -> FakeModel:
            calls.append((device, t3_model))
            return model

    module = types.ModuleType("chatterbox.mtl_tts")
    module.ChatterboxMultilingualTTS = NewApiModel
    monkeypatch.setitem(sys.modules, "chatterbox", types.ModuleType("chatterbox"))
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", module)

    loaded = ChatterboxTTS._default_model_factory("cuda", "v3")

    assert loaded is model
    assert calls == [("cuda", "v3")]


def test_default_model_factory_supports_older_chatterbox_api(monkeypatch) -> None:
    import sys
    import types

    calls: list[str] = []
    model = FakeModel()

    class OldApiModel:
        @classmethod
        def from_pretrained(cls, *, device: str) -> FakeModel:
            calls.append(device)
            return model

    module = types.ModuleType("chatterbox.mtl_tts")
    module.ChatterboxMultilingualTTS = OldApiModel
    monkeypatch.setitem(sys.modules, "chatterbox", types.ModuleType("chatterbox"))
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", module)

    loaded = ChatterboxTTS._default_model_factory("cuda", "v3")

    assert loaded is model
    assert calls == ["cuda"]


class FakeProcessClient:
    def __init__(self) -> None:
        self.is_running = False
        self.warmups = 0
        self.generated: list[str] = []
        self.cancelled = 0
        self.closed = 0

    def warm_up(self) -> str:
        self.warmups += 1
        self.is_running = True
        return "cuda"

    def generate(self, text: str) -> tuple[list[float], int]:
        self.is_running = True
        self.generated.append(text)
        return [0.0, 0.2, -0.2], 24000

    def cancel(self) -> None:
        self.cancelled += 1
        self.is_running = False

    def close(self) -> None:
        self.closed += 1
        self.is_running = False


def test_chatterbox_default_path_uses_isolated_process_client() -> None:
    player = FakePlayer()
    process_client = FakeProcessClient()
    tts = ChatterboxTTS(
        device="auto",
        audio_player=player,
        process_client=process_client,
    )

    tts.warm_up()
    assert tts.is_model_loaded is True

    tts.speak("Pronto, senhor.")

    assert process_client.generated == ["Pronto, senhor."]
    assert player.calls == [([0.0, 0.2, -0.2], 24000)]
    tts.close()


def test_chatterbox_stop_terminates_isolated_generation_process() -> None:
    process_client = FakeProcessClient()
    tts = ChatterboxTTS(process_client=process_client)

    tts.warm_up()
    tts.stop()

    assert process_client.cancelled == 1
    assert process_client.is_running is False
    tts.close()
