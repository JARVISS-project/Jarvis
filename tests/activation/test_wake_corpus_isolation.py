import importlib.util
from pathlib import Path

import pytest

from scripts.train_wake_verifier import _reject_holdout_training_inputs


ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wav(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_training_rejects_holdout_path_before_model_loading(
    tmp_path: Path,
) -> None:
    holdout = _wav(
        tmp_path / "voice-holdout-independent" / "positive" / "x.wav",
        b"not-real-audio-needed-for-path-test",
    )
    with pytest.raises(ValueError, match="Holdout corpus"):
        _reject_holdout_training_inputs([holdout])


def test_training_accepts_explicit_development_path(tmp_path: Path) -> None:
    development = _wav(
        tmp_path / "voice-dev-nearmiss" / "positive" / "x.wav",
        b"dev",
    )
    _reject_holdout_training_inputs([development])


def test_corpus_isolation_detects_sha_collision_across_paths(
    tmp_path: Path,
) -> None:
    isolation = _load_script("check_wake_corpus_isolation.py")
    dev = _wav(tmp_path / "dev" / "a.wav", b"same-bytes")
    holdout = _wav(tmp_path / "holdout" / "b.wav", b"same-bytes")

    result = isolation.main(
        [
            "--development",
            str(dev.parent),
            "--holdout",
            str(holdout.parent),
        ]
    )

    assert result == 1


def test_corpus_isolation_passes_for_distinct_audio(tmp_path: Path) -> None:
    isolation = _load_script("check_wake_corpus_isolation.py")
    dev = _wav(tmp_path / "dev" / "a.wav", b"development")
    holdout = _wav(tmp_path / "holdout" / "b.wav", b"holdout")

    result = isolation.main(
        [
            "--development",
            str(dev.parent),
            "--holdout",
            str(holdout.parent),
        ]
    )

    assert result == 0
