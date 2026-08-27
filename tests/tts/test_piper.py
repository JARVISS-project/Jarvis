from pathlib import Path
from types import SimpleNamespace

import numpy as np

from jarvis.tts.piper import PiperTTS


class FakeVoice:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.config = SimpleNamespace(length_scale=1.0)

    def synthesize(self, text: str):  # type: ignore[no-untyped-def]
        self.calls.append(text)
        return [
            SimpleNamespace(
                sample_rate=22050,
                audio_float_array=np.array([0.0, 0.25, -0.25], dtype=np.float32),
            )
        ]


class FakePlayer:
    def __init__(self) -> None:
        self.calls: list[tuple[np.ndarray, int]] = []
        self.stopped = False

    def play(self, audio, sample_rate: int) -> None:  # type: ignore[no-untyped-def]
        self.calls.append((np.asarray(audio), sample_rate))

    def stop(self) -> None:
        self.stopped = True


def _ready_paths(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "razo.onnx"
    config = tmp_path / "razo.onnx.json"
    model.write_bytes(b"fake")
    config.write_text("{}", encoding="utf-8")
    return model, config


def test_piper_is_lazy_and_reuses_loaded_voice(tmp_path: Path) -> None:
    model, config = _ready_paths(tmp_path)
    fake_voice = FakeVoice()
    factory_calls = 0

    def factory(_model: Path, _config: Path | None) -> FakeVoice:
        nonlocal factory_calls
        factory_calls += 1
        return fake_voice

    player = FakePlayer()
    tts = PiperTTS(
        model_path=model,
        config_path=config,
        voice_factory=factory,
        audio_player=player,
    )

    assert tts.is_model_loaded is False
    tts.speak("Olá")
    tts.speak("Tudo certo")

    assert factory_calls == 1
    assert fake_voice.calls == ["Olá", "Tudo certo"]
    assert len(player.calls) == 2
    assert all(sample_rate == 22050 for _audio, sample_rate in player.calls)
    tts.close()


def test_piper_reports_not_ready_when_model_is_missing(tmp_path: Path) -> None:
    tts = PiperTTS(
        model_path=tmp_path / "missing.onnx",
        config_path=tmp_path / "missing.onnx.json",
        voice_factory=lambda _model, _config: FakeVoice(),
        audio_player=FakePlayer(),
    )

    assert tts.is_ready is False
    tts.close()


def test_piper_appends_silence_tail_before_playback(tmp_path: Path) -> None:
    model, config = _ready_paths(tmp_path)
    player = FakePlayer()
    tts = PiperTTS(
        model_path=model,
        config_path=config,
        voice_factory=lambda _model, _config: FakeVoice(),
        audio_player=player,
        tail_silence_seconds=0.30,
    )

    tts.speak("Até mais")

    assert len(player.calls) == 1
    audio, sample_rate = player.calls[0]
    assert sample_rate == 22050
    expected_tail = int(sample_rate * 0.30)
    assert len(audio) == 3 + expected_tail
    assert np.all(audio[-expected_tail:] == 0.0)
    tts.close()


def test_piper_can_disable_silence_tail(tmp_path: Path) -> None:
    model, config = _ready_paths(tmp_path)
    player = FakePlayer()
    tts = PiperTTS(
        model_path=model,
        config_path=config,
        voice_factory=lambda _model, _config: FakeVoice(),
        audio_player=player,
        tail_silence_seconds=0.0,
    )

    tts.speak("Pronto")

    audio, _sample_rate = player.calls[0]
    assert len(audio) == 3
    tts.close()


def test_piper_exposes_last_synthesis_time(tmp_path: Path) -> None:
    model, config = _ready_paths(tmp_path)
    tts = PiperTTS(
        model_path=model,
        config_path=config,
        voice_factory=lambda _model, _config: FakeVoice(),
        audio_player=FakePlayer(),
    )

    assert tts.last_synthesis_seconds is None
    tts.speak("Olá")

    assert tts.last_synthesis_seconds is not None
    assert tts.last_synthesis_seconds >= 0.0
    tts.close()


def test_piper_applies_conversational_length_scale(tmp_path: Path) -> None:
    model, config = _ready_paths(tmp_path)
    fake_voice = FakeVoice()
    tts = PiperTTS(
        model_path=model,
        config_path=config,
        voice_factory=lambda _model, _config: fake_voice,
        audio_player=FakePlayer(),
        length_scale=1.18,
    )

    tts.warm_up()

    assert fake_voice.config.length_scale == 1.18
    tts.close()


def test_piper_inserts_silence_between_sentence_chunks(tmp_path: Path) -> None:
    model, config = _ready_paths(tmp_path)

    class TwoSentenceVoice(FakeVoice):
        def synthesize(self, text: str):  # type: ignore[no-untyped-def]
            self.calls.append(text)
            return [
                SimpleNamespace(
                    sample_rate=1000,
                    audio_float_array=np.array([0.1, 0.2], dtype=np.float32),
                ),
                SimpleNamespace(
                    sample_rate=1000,
                    audio_float_array=np.array([0.3, 0.4], dtype=np.float32),
                ),
            ]

    player = FakePlayer()
    tts = PiperTTS(
        model_path=model,
        config_path=config,
        voice_factory=lambda _model, _config: TwoSentenceVoice(),
        audio_player=player,
        sentence_silence_seconds=0.18,
        tail_silence_seconds=0.0,
    )

    tts.speak("Primeira frase. Segunda frase.")

    audio, sample_rate = player.calls[0]
    assert sample_rate == 1000
    assert len(audio) == 2 + 180 + 2
    assert np.all(audio[2:182] == 0.0)
    tts.close()
