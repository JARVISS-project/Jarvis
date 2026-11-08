from pathlib import Path

import numpy as np
import pytest

from jarvis.activation.verifier import (
    SafeWakeVerifier,
    load_safe_wake_verifier,
)


def _write_verifier(
    path: Path,
    *,
    model_name: object = "hey_jarvis",
    feature_shape: tuple[int, ...] = (1, 2),
    mean: tuple[float, ...] = (0.0, 0.0),
    scale: tuple[float, ...] = (1.0, 1.0),
    coefficient: tuple[float, ...] = (1.0, 1.0),
    intercept: float = 0.0,
) -> None:
    with path.open("wb") as target:
        np.savez_compressed(
            target,
            format_version=np.asarray(1, dtype=np.int64),
            model_name=np.asarray(model_name),
            feature_shape=np.asarray(feature_shape, dtype=np.int64),
            mean=np.asarray(mean, dtype=np.float64),
            scale=np.asarray(scale, dtype=np.float64),
            coefficient=np.asarray(coefficient, dtype=np.float64),
            intercept=np.asarray(intercept, dtype=np.float64),
        )


def test_safe_verifier_loads_numeric_npz_and_scores(tmp_path: Path) -> None:
    path = tmp_path / "verifier.npz"
    _write_verifier(path)

    verifier = load_safe_wake_verifier(path)

    assert verifier.model_name == "hey_jarvis"
    assert verifier.feature_shape == (1, 2)
    assert verifier.score([[0.0, 0.0]]) == pytest.approx(0.5)
    assert verifier.score([[1.0, 1.0]]) == pytest.approx(
        0.8807970779
    )


def test_safe_verifier_rejects_pickle_backed_object_arrays(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unsafe.npz"
    _write_verifier(path, model_name={"unsafe": "object"})

    with pytest.raises(ValueError, match="Wake verifier local inválido"):
        load_safe_wake_verifier(path)


def test_safe_verifier_rejects_incompatible_vectors(tmp_path: Path) -> None:
    path = tmp_path / "bad-shape.npz"
    _write_verifier(path, coefficient=(1.0,))

    with pytest.raises(ValueError, match="Vetores incompatíveis"):
        load_safe_wake_verifier(path)


def test_safe_verifier_rejects_invalid_scale(tmp_path: Path) -> None:
    path = tmp_path / "bad-scale.npz"
    _write_verifier(path, scale=(1.0, 0.0))

    with pytest.raises(ValueError, match="escala inválida"):
        load_safe_wake_verifier(path)


def test_safe_verifier_rejects_feature_shape_at_score_time() -> None:
    verifier = SafeWakeVerifier(
        model_name="hey_jarvis",
        feature_shape=(1, 2),
        mean=np.asarray([0.0, 0.0]),
        scale=np.asarray([1.0, 1.0]),
        coefficient=np.asarray([1.0, 1.0]),
        intercept=0.0,
    )

    with pytest.raises(ValueError, match="Formato de features"):
        verifier.score([[1.0, 2.0, 3.0]])


def test_safe_verifier_rejects_non_npz_file(tmp_path: Path) -> None:
    path = tmp_path / "not-an-npz.npz"
    path.write_bytes(b"not a zip archive")

    with pytest.raises(ValueError, match="Wake verifier local inválido"):
        load_safe_wake_verifier(path)
