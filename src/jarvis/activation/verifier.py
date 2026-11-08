from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import zipfile

import numpy as np


VERIFIER_FORMAT_VERSION = 1
MAX_VERIFIER_FILE_BYTES = 1_000_000
MAX_VERIFIER_UNCOMPRESSED_BYTES = 2_000_000
MAX_VERIFIER_ARCHIVE_MEMBERS = 16


@dataclass(frozen=True, slots=True)
class SafeWakeVerifier:
    model_name: str
    feature_shape: tuple[int, ...]
    mean: np.ndarray
    scale: np.ndarray
    coefficient: np.ndarray
    intercept: float

    def score(self, features: object) -> float:
        array = np.asarray(features, dtype=np.float64)
        if tuple(array.shape[-len(self.feature_shape):]) != self.feature_shape:
            raise ValueError(
                "Formato de features incompatível com o wake verifier."
            )
        flattened = array.reshape(-1)
        if flattened.size != self.mean.size:
            raise ValueError(
                "Quantidade de features incompatível com o wake verifier."
            )
        normalized = (flattened - self.mean) / self.scale
        logit = float(np.dot(normalized, self.coefficient) + self.intercept)
        if logit >= 0:
            probability = 1.0 / (1.0 + math.exp(-logit))
        else:
            exp_logit = math.exp(logit)
            probability = exp_logit / (1.0 + exp_logit)
        return max(0.0, min(1.0, probability))


def load_safe_wake_verifier(
    path: Path | str,
    *,
    maximum_file_bytes: int = MAX_VERIFIER_FILE_BYTES,
) -> SafeWakeVerifier:
    verifier_path = Path(path)
    if not verifier_path.is_file():
        raise FileNotFoundError(
            f"Wake verifier não encontrado: {verifier_path}"
        )
    if verifier_path.stat().st_size > maximum_file_bytes:
        raise ValueError("Wake verifier excede o tamanho máximo permitido.")

    try:
        with zipfile.ZipFile(verifier_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_VERIFIER_ARCHIVE_MEMBERS:
                raise ValueError(
                    "Wake verifier possui membros demais no arquivo."
                )
            if (
                sum(member.file_size for member in members)
                > MAX_VERIFIER_UNCOMPRESSED_BYTES
            ):
                raise ValueError(
                    "Wake verifier excede o tamanho descomprimido permitido."
                )
    except zipfile.BadZipFile as error:
        raise ValueError("Wake verifier local inválido.") from error

    try:
        with np.load(verifier_path, allow_pickle=False) as payload:
            required = {
                "format_version",
                "model_name",
                "feature_shape",
                "mean",
                "scale",
                "coefficient",
                "intercept",
            }
            missing = required.difference(payload.files)
            if missing:
                raise ValueError(
                    "Wake verifier incompleto: "
                    + ", ".join(sorted(missing))
                )

            version = int(np.asarray(payload["format_version"]).item())
            if version != VERIFIER_FORMAT_VERSION:
                raise ValueError(
                    f"Versão de wake verifier não suportada: {version}."
                )

            model_name = str(np.asarray(payload["model_name"]).item()).strip()
            if not model_name or len(model_name) > 128:
                raise ValueError("Nome de modelo inválido no wake verifier.")

            feature_shape_values = np.asarray(
                payload["feature_shape"], dtype=np.int64
            ).reshape(-1)
            if (
                feature_shape_values.size == 0
                or feature_shape_values.size > 4
                or np.any(feature_shape_values <= 0)
            ):
                raise ValueError("Shape inválido no wake verifier.")
            feature_shape = tuple(int(value) for value in feature_shape_values)

            mean = np.asarray(payload["mean"], dtype=np.float64).reshape(-1)
            scale = np.asarray(payload["scale"], dtype=np.float64).reshape(-1)
            coefficient = np.asarray(
                payload["coefficient"], dtype=np.float64
            ).reshape(-1)
            intercept = float(np.asarray(payload["intercept"]).item())
    except (OSError, ValueError, TypeError) as error:
        if isinstance(error, ValueError) and str(error).startswith(
            (
                "Wake verifier",
                "Versão de wake verifier",
                "Nome de modelo",
                "Shape inválido",
            )
        ):
            raise
        raise ValueError("Wake verifier local inválido.") from error

    expected_size = math.prod(feature_shape)
    if expected_size <= 0 or expected_size > 100_000:
        raise ValueError("Shape inválido no wake verifier.")
    if not (
        mean.size
        == scale.size
        == coefficient.size
        == expected_size
    ):
        raise ValueError("Vetores incompatíveis no wake verifier.")
    if (
        not np.all(np.isfinite(mean))
        or not np.all(np.isfinite(scale))
        or not np.all(np.isfinite(coefficient))
        or not math.isfinite(intercept)
    ):
        raise ValueError("Wake verifier contém valores não finitos.")
    if np.any(scale <= 0):
        raise ValueError("Wake verifier contém escala inválida.")

    for array in (mean, scale, coefficient):
        array.setflags(write=False)

    return SafeWakeVerifier(
        model_name=model_name,
        feature_shape=feature_shape,
        mean=mean,
        scale=scale,
        coefficient=coefficient,
        intercept=intercept,
    )
