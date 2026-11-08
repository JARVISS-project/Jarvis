from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest

from jarvis.activation.openwakeword import OpenWakeWordDetector
from jarvis.activation.verifier import SafeWakeVerifier


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


class FakeFeaturePreprocessor:
    def __init__(self, features: list[list[float]]) -> None:
        self.features = np.asarray(features, dtype=np.float64)

    def get_features(self, _model_input: int) -> np.ndarray:
        return self.features


class FakeVerifierOpenWakeWordModel(FakeOpenWakeWordModel):
    def __init__(
        self,
        scores: list[dict[str, float]],
        features: list[list[float]],
    ) -> None:
        super().__init__(scores)
        self.preprocessor = FakeFeaturePreprocessor(features)
        self.model_inputs = {"hey_jarvis": 1}


def _test_verifier(
    *,
    feature_shape: tuple[int, ...] = (1, 2),
    coefficient: tuple[float, ...] = (2.0, 2.0),
) -> SafeWakeVerifier:
    size = int(np.prod(feature_shape))
    return SafeWakeVerifier(
        model_name="hey_jarvis",
        feature_shape=feature_shape,
        mean=np.zeros(size, dtype=np.float64),
        scale=np.ones(size, dtype=np.float64),
        coefficient=np.asarray(coefficient, dtype=np.float64),
        intercept=0.0,
    )


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
    model = FakeOpenWakeWordModel(
        [{"hey_jarvis": 0.8}, {"hey_jarvis": 0.82}]
    )
    detector = OpenWakeWordDetector(
        threshold=0.5,
        model_factory=lambda **_kwargs: model,
    )

    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is True


def test_threshold_is_configurable() -> None:
    model = FakeOpenWakeWordModel(
        [{"hey_jarvis": 0.7}, {"hey_jarvis": 0.7}]
    )
    strict_detector = OpenWakeWordDetector(
        threshold=0.8,
        minimum_consecutive_detections=1,
        model_factory=lambda **_kwargs: model,
    )
    permissive_detector = OpenWakeWordDetector(
        threshold=0.6,
        minimum_consecutive_detections=1,
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
        [{"hey_jarvis": 0.8}, {"hey_jarvis": 0.9}, {"hey_jarvis": 0.95},
         {"hey_jarvis": 0.1}, {"hey_jarvis": 0.8}, {"hey_jarvis": 0.9}]
    )
    detector = OpenWakeWordDetector(
        threshold=0.5,
        model_factory=lambda **_kwargs: model,
    )

    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is True
    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is True


def test_verifier_promotes_candidate_after_two_verified_frames() -> None:
    verifier = _test_verifier()
    model = FakeVerifierOpenWakeWordModel(
        [{"hey_jarvis": 0.30}, {"hey_jarvis": 0.31}],
        [[1.0, 1.0]],
    )
    detector = OpenWakeWordDetector(
        threshold=0.5,
        verifier_path="local-verifier.npz",
        verifier_candidate_threshold=0.20,
        verifier_threshold=0.70,
        verifier_strong_threshold=0.99,
        model_factory=lambda **_kwargs: model,
        verifier_loader=lambda _path: verifier,
    )

    assert detector.verifier_enabled is True
    assert detector.activation_threshold == pytest.approx(0.70)
    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is True
    assert detector.last_base_score == pytest.approx(0.31)
    assert detector.last_verifier_score is not None
    assert detector.last_verifier_score > 0.9


def test_verifier_rejects_near_miss_even_with_high_base_score() -> None:
    verifier = _test_verifier()
    model = FakeVerifierOpenWakeWordModel(
        [{"hey_jarvis": 0.90}, {"hey_jarvis": 0.92}],
        [[-1.0, -1.0]],
    )
    detector = OpenWakeWordDetector(
        verifier_path="local-verifier.npz",
        verifier_candidate_threshold=0.20,
        verifier_threshold=0.70,
        model_factory=lambda **_kwargs: model,
        verifier_loader=lambda _path: verifier,
    )

    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is False
    assert detector.last_verifier_score is not None
    assert detector.last_verifier_score < 0.1


def test_verifier_feature_mismatch_fails_closed() -> None:
    verifier = _test_verifier(
        feature_shape=(2, 2),
        coefficient=(1.0, 1.0, 1.0, 1.0),
    )
    model = FakeVerifierOpenWakeWordModel(
        [{"hey_jarvis": 0.95}],
        [[1.0, 1.0]],
    )
    detector = OpenWakeWordDetector(
        minimum_consecutive_detections=1,
        verifier_path="local-verifier.npz",
        verifier_candidate_threshold=0.20,
        verifier_threshold=0.70,
        model_factory=lambda **_kwargs: model,
        verifier_loader=lambda _path: verifier,
    )

    assert detector.process([0] * 1280) is False
    assert detector.last_score == 0.0
    assert detector.last_verifier_error is not None


def test_verifier_is_not_called_below_candidate_threshold() -> None:
    verifier = _test_verifier()
    model = FakeVerifierOpenWakeWordModel(
        [{"hey_jarvis": 0.19}],
        [[1.0, 1.0]],
    )
    detector = OpenWakeWordDetector(
        minimum_consecutive_detections=1,
        verifier_path="local-verifier.npz",
        verifier_candidate_threshold=0.20,
        verifier_threshold=0.70,
        model_factory=lambda **_kwargs: model,
        verifier_loader=lambda _path: verifier,
    )

    assert detector.process([0] * 1280) is False
    assert detector.last_base_score == pytest.approx(0.19)
    assert detector.last_verifier_score is None


def test_verifier_strong_score_can_confirm_in_one_frame() -> None:
    verifier = _test_verifier()
    model = FakeVerifierOpenWakeWordModel(
        [{"hey_jarvis": 0.30}],
        [[1.0, 1.0]],
    )
    detector = OpenWakeWordDetector(
        verifier_path="local-verifier.npz",
        verifier_candidate_threshold=0.20,
        verifier_threshold=0.70,
        verifier_strong_threshold=0.85,
        minimum_consecutive_detections=2,
        model_factory=lambda **_kwargs: model,
        verifier_loader=lambda _path: verifier,
    )

    assert detector.process([0] * 1280) is True
    assert detector.last_confirmation_frames == 1


def test_verifier_score_below_strong_threshold_still_needs_two_frames() -> None:
    verifier = SafeWakeVerifier(
        model_name="hey_jarvis",
        feature_shape=(1, 2),
        mean=np.asarray([0.0, 0.0]),
        scale=np.asarray([1.0, 1.0]),
        coefficient=np.asarray([0.75, 0.75]),
        intercept=0.0,
    )
    model = FakeVerifierOpenWakeWordModel(
        [{"hey_jarvis": 0.30}, {"hey_jarvis": 0.31}],
        [[1.0, 1.0]],
    )
    detector = OpenWakeWordDetector(
        verifier_path="local-verifier.npz",
        verifier_candidate_threshold=0.20,
        verifier_threshold=0.70,
        verifier_strong_threshold=0.85,
        minimum_consecutive_detections=2,
        model_factory=lambda **_kwargs: model,
        verifier_loader=lambda _path: verifier,
    )

    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is True
    assert detector.last_confirmation_frames == 2


def test_verifier_strong_threshold_must_not_be_below_regular_threshold() -> None:
    with pytest.raises(ValueError, match="verifier_strong_threshold"):
        OpenWakeWordDetector(
            verifier_threshold=0.80,
            verifier_strong_threshold=0.79,
            model_factory=lambda **_kwargs: FakeOpenWakeWordModel([]),
        )


def test_invalid_optional_verifier_falls_back_to_base_detector() -> None:
    model = FakeOpenWakeWordModel(
        [{"hey_jarvis": 0.80}, {"hey_jarvis": 0.82}]
    )

    def broken_loader(_path):
        raise ValueError("verifier corrompido")

    detector = OpenWakeWordDetector(
        verifier_path="broken-verifier.npz",
        verifier_loader=broken_loader,
        model_factory=lambda **_kwargs: model,
    )

    assert detector.verifier_enabled is False
    assert detector.verifier_load_error == "verifier corrompido"
    assert detector.activation_threshold == pytest.approx(0.5)
    assert detector.process([0] * 1280) is False
    assert detector.process([0] * 1280) is True
