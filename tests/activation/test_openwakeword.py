from collections.abc import Sequence
from typing import Any

import pytest

from jarvis.activation.openwakeword import OpenWakeWordDetector


class FakeOpenWakeWordModel:
    def __init__(self, scores: list[dict[str, float]]) -> None:
        self.scores = scores
        self.frames: list[Sequence[int]] = []
        self.reset_called = False

    def predict(self, frame: Sequence[int]) -> dict[str, float]:
        self.frames.append(frame)
        return self.scores.pop(0)

    def reset(self) -> None:
        self.reset_called = True


def test_detector_initializes_hey_jarvis_with_cpu_onnx() -> None:
    arguments: dict[str, Any] = {}
    model = FakeOpenWakeWordModel([{"hey_jarvis": 0.1}])

    def factory(**kwargs: Any) -> FakeOpenWakeWordModel:
        arguments.update(kwargs)
        return model

    detector = OpenWakeWordDetector(model_factory=factory)

    assert detector.frame_length == 1280
    assert detector.sample_rate == 16000
    assert arguments == {
        "wakeword_models": ["hey_jarvis"],
        "inference_framework": "onnx",
        "device": "cpu",
        "ncpu": 1,
    }


def test_normal_frame_does_not_activate() -> None:
    model = FakeOpenWakeWordModel([{"hey_jarvis": 0.49}])
    detector = OpenWakeWordDetector(
        threshold=0.5,
        model_factory=lambda **_kwargs: model,
    )

    assert detector.process([0] * 1280) is False


def test_score_above_threshold_activates() -> None:
    model = FakeOpenWakeWordModel([{"hey_jarvis": 0.8}])
    detector = OpenWakeWordDetector(
        threshold=0.5,
        model_factory=lambda **_kwargs: model,
    )

    assert detector.process([0] * 1280) is True


def test_threshold_is_configurable() -> None:
    model = FakeOpenWakeWordModel(
        [{"hey_jarvis": 0.7}, {"hey_jarvis": 0.7}]
    )
    strict_detector = OpenWakeWordDetector(
        threshold=0.8,
        model_factory=lambda **_kwargs: model,
    )
    permissive_detector = OpenWakeWordDetector(
        threshold=0.6,
        model_factory=lambda **_kwargs: model,
    )

    assert strict_detector.process([0] * 1280) is False
    assert permissive_detector.process([0] * 1280) is True


def test_local_onnx_model_path_can_be_configured() -> None:
    arguments: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeOpenWakeWordModel:
        arguments.update(kwargs)
        return FakeOpenWakeWordModel([])

    OpenWakeWordDetector(
        model_path="models/hey_jarvis.onnx",
        model_factory=factory,
    )

    assert arguments["wakeword_models"] == ["models/hey_jarvis.onnx"]


def test_detector_releases_model_resources() -> None:
    model = FakeOpenWakeWordModel([])
    detector = OpenWakeWordDetector(
        model_factory=lambda **_kwargs: model,
    )

    detector.close()
    detector.close()

    assert model.reset_called is True


def test_detector_does_not_reuse_continuous_positive_score() -> None:
    model = FakeOpenWakeWordModel(
        [{"hey_jarvis": 0.8}, {"hey_jarvis": 0.9}, {"hey_jarvis": 0.1},
         {"hey_jarvis": 0.8}]
    )
    detector = OpenWakeWordDetector(
        threshold=0.5,
        model_factory=lambda **_kwargs: model,
    )

    assert detector.process([0] * 1280) is True
    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is True
