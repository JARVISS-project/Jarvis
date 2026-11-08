from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from jarvis.activation.verifier import load_safe_wake_verifier
from jarvis.config import JarvisConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the local wake verifier and optionally prove that a "
            "runtime copy is byte-identical."
        )
    )
    parser.add_argument("--primary", type=Path, default=None)
    parser.add_argument("--runtime-copy", type=Path, default=None)
    return parser


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = JarvisConfig()
    primary = (args.primary or config.wake_word_verifier_path).resolve()

    verifier = load_safe_wake_verifier(primary)
    primary_hash = sha256(primary)
    print(
        "wake verifier "
        f"path={primary} model={verifier.model_name} "
        f"shape={verifier.feature_shape} sha256={primary_hash} "
        f"candidate={config.wake_word_verifier_candidate_threshold:.3f} "
        f"threshold={config.wake_word_verifier_threshold:.3f} "
        f"strong={config.wake_word_verifier_strong_threshold:.3f}"
    )

    if args.runtime_copy is None:
        return 0

    runtime_copy = args.runtime_copy.resolve()
    runtime_verifier = load_safe_wake_verifier(runtime_copy)
    runtime_hash = sha256(runtime_copy)
    same = (
        primary_hash == runtime_hash
        and verifier.model_name == runtime_verifier.model_name
        and verifier.feature_shape == runtime_verifier.feature_shape
    )
    print(
        "runtime verifier parity "
        f"path={runtime_copy} sha256={runtime_hash} "
        f"result={'PASS' if same else 'FAIL'}"
    )
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
