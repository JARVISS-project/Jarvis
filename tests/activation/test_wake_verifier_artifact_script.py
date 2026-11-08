import importlib.util
import shutil
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    path = ROOT / "scripts" / "check_wake_verifier_artifact.py"
    spec = importlib.util.spec_from_file_location(
        "check_wake_verifier_artifact", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verifier(path: Path, intercept: float = 0.0) -> Path:
    feature_shape = (1, 2)
    mean = np.asarray([0.0, 0.0], dtype=np.float64)
    scale = np.asarray([1.0, 1.0], dtype=np.float64)
    coefficient = np.asarray([0.1, 0.2], dtype=np.float64)
    with path.open("wb") as target:
        np.savez_compressed(
            target,
            format_version=np.asarray(1, dtype=np.int64),
            model_name=np.asarray("hey_jarvis"),
            feature_shape=np.asarray(feature_shape, dtype=np.int64),
            mean=mean,
            scale=scale,
            coefficient=coefficient,
            intercept=np.asarray(intercept, dtype=np.float64),
        )
    return path


def test_artifact_script_accepts_identical_runtime_copy(
    tmp_path: Path,
) -> None:
    script = _load_script()
    primary = _verifier(tmp_path / "primary.npz")
    runtime = tmp_path / "runtime.npz"
    shutil.copyfile(primary, runtime)

    assert script.main(
        ["--primary", str(primary), "--runtime-copy", str(runtime)]
    ) == 0


def test_artifact_script_rejects_mismatched_runtime_copy(
    tmp_path: Path,
) -> None:
    script = _load_script()
    primary = _verifier(tmp_path / "primary.npz")
    runtime = _verifier(tmp_path / "runtime.npz", intercept=1.0)

    assert script.main(
        ["--primary", str(primary), "--runtime-copy", str(runtime)]
    ) == 1
