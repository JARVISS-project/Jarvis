from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from jarvis.activation.verifier import VERIFIER_FORMAT_VERSION
from jarvis.config import JarvisConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Treina um verifier local de Hey Jarvis e exporta somente "
            "parâmetros numéricos em NPZ, sem pickle."
        )
    )
    parser.add_argument(
        "--positive",
        action="append",
        type=Path,
        default=[],
        help="WAV ou diretório com exemplos de Hey Jarvis.",
    )
    parser.add_argument(
        "--negative",
        action="append",
        type=Path,
        default=[],
        help="WAV ou diretório com fala/ruído que não deve ativar.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Destino .npz. Default: config wake_word_verifier_path.",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument(
        "--positive-feature-threshold",
        type=float,
        default=0.15,
    )
    parser.add_argument(
        "--augmentations",
        type=int,
        default=2,
        help="Variações de alinhamento por clip positivo.",
    )
    parser.add_argument("--seed", type=int, default=0)
    return parser


def _wav_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix.casefold() != ".wav":
                raise ValueError(f"{path}: arquivo não é WAV.")
            files.append(path)
            continue
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*.wav")
                if candidate.is_file()
            )
            continue
        raise ValueError(f"{path}: caminho não encontrado.")
    return sorted(dict.fromkeys(files))


def _reject_holdout_training_inputs(paths: list[Path]) -> None:
    for path in paths:
        lowered_parts = {part.casefold() for part in path.resolve().parts}
        if any("holdout" in part for part in lowered_parts):
            raise ValueError(
                "Holdout corpus cannot be used for verifier training. "
                f"Reclassify/move it into an explicit development dataset first: {path}"
            )


def _find_pipeline_step(pipeline: object, *attributes: str) -> object:
    named_steps = getattr(pipeline, "named_steps", None)
    if not isinstance(named_steps, dict):
        raise RuntimeError("Pipeline do verifier não expõe named_steps.")
    for step in named_steps.values():
        if all(hasattr(step, attribute) for attribute in attributes):
            return step
    raise RuntimeError(
        "Pipeline do verifier não contém os parâmetros esperados."
    )


def _extract_features(
    paths: list[Path],
    *,
    model: object,
    model_name: str,
    threshold: float,
    augmentations: int,
    label: str,
) -> np.ndarray:
    try:
        from openwakeword.custom_verifier_model import (
            get_reference_clip_features,
        )
    except ImportError as error:
        raise RuntimeError(
            "openWakeWord não instalado. Instale o extra 'voice'."
        ) from error

    blocks: list[np.ndarray] = []
    for path in paths:
        reset = getattr(model, "reset", None)
        if callable(reset):
            reset()
        features = get_reference_clip_features(
            str(path),
            model,
            model_name,
            threshold=threshold,
            N=augmentations,
        )
        if features.shape[0] == 0:
            print(f"{label} SKIP features=0 {path}")
            continue
        blocks.append(np.asarray(features, dtype=np.float64))
        print(f"{label} features={features.shape[0]} {path}")

    if not blocks:
        raise RuntimeError(
            f"Nenhuma feature {label.casefold()} foi extraída."
        )
    return np.vstack(blocks)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.positive or not args.negative:
        parser.error("informe --positive e --negative")
    if not 0.0 <= args.positive_feature_threshold <= 1.0:
        parser.error("--positive-feature-threshold deve estar entre 0 e 1")
    if args.augmentations <= 0:
        parser.error("--augmentations deve ser positivo")

    try:
        positive = _wav_files(args.positive)
        negative = _wav_files(args.negative)
    except ValueError as error:
        parser.error(str(error))
    if not positive or not negative:
        parser.error("é necessário ao menos um WAV positivo e um negativo")
    try:
        _reject_holdout_training_inputs(positive + negative)
    except ValueError as error:
        parser.error(str(error))

    try:
        import openwakeword
        from openwakeword.custom_verifier_model import train_verifier_model
    except ImportError as error:
        raise RuntimeError(
            "openWakeWord não instalado. Instale o extra 'voice'."
        ) from error

    config = JarvisConfig()
    model_name = args.model or config.wake_word_model
    model_path = args.model_path or config.wake_word_model_path
    model_identifier = str(model_path or model_name)
    output = args.output or config.wake_word_verifier_path
    if output.suffix.casefold() != ".npz":
        parser.error("--output deve terminar em .npz")

    np.random.seed(args.seed)
    model = openwakeword.Model(
        wakeword_models=[model_identifier],
        inference_framework="onnx",
        device="cpu",
        ncpu=1,
    )
    model_keys = list(model.models.keys())
    if len(model_keys) != 1:
        raise RuntimeError(
            "Treino do verifier requer exatamente um modelo de wake word."
        )
    resolved_model_name = model_keys[0]

    positive_features = _extract_features(
        positive,
        model=model,
        model_name=resolved_model_name,
        threshold=args.positive_feature_threshold,
        augmentations=args.augmentations,
        label="POS",
    )
    negative_features = _extract_features(
        negative,
        model=model,
        model_name=resolved_model_name,
        threshold=0.0,
        augmentations=args.augmentations,
        label="NEG",
    )
    if positive_features.shape[1:] != negative_features.shape[1:]:
        raise RuntimeError(
            "Features positivas e negativas possuem shapes incompatíveis."
        )

    labels = np.concatenate(
        (
            np.ones(positive_features.shape[0], dtype=np.int64),
            np.zeros(negative_features.shape[0], dtype=np.int64),
        )
    )

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.preprocessing import FunctionTransformer
    from openwakeword.custom_verifier_model import flatten_features

    clf = LogisticRegression(random_state=0, max_iter=2000, C=0.001, class_weight="balanced")
    pipeline = make_pipeline(FunctionTransformer(flatten_features), StandardScaler(), clf)
    pipeline.fit(np.vstack((positive_features, negative_features)), labels)
    scaler = _find_pipeline_step(pipeline, "mean_", "scale_")
    classifier = _find_pipeline_step(pipeline, "coef_", "intercept_")

    mean = np.asarray(scaler.mean_, dtype=np.float64).reshape(-1)
    scale = np.asarray(scaler.scale_, dtype=np.float64).reshape(-1)
    coefficient = np.asarray(
        classifier.coef_[0], dtype=np.float64
    ).reshape(-1)
    intercept = float(np.asarray(classifier.intercept_).reshape(-1)[0])
    feature_shape = np.asarray(
        positive_features.shape[1:], dtype=np.int64
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        with temporary.open("wb") as target:
            np.savez_compressed(
                target,
                format_version=np.asarray(
                    VERIFIER_FORMAT_VERSION, dtype=np.int64
                ),
                model_name=np.asarray(resolved_model_name),
                feature_shape=feature_shape,
                mean=mean,
                scale=scale,
                coefficient=coefficient,
                intercept=np.asarray(intercept, dtype=np.float64),
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

    print(
        "verifier saved "
        f"path={output} model={resolved_model_name} "
        f"positive_clips={len(positive)} "
        f"negative_clips={len(negative)} "
        f"positive_features={positive_features.shape[0]} "
        f"negative_features={negative_features.shape[0]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
