from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that development/training wake corpora do not overlap "
            "with holdout corpora by resolved path or SHA-256."
        )
    )
    parser.add_argument(
        "--development",
        action="append",
        type=Path,
        default=[],
        help="Development WAV file or directory. Repeatable.",
    )
    parser.add_argument(
        "--holdout",
        action="append",
        type=Path,
        default=[],
        help="Holdout WAV file or directory. Repeatable.",
    )
    parser.add_argument("--json-output", type=Path, default=None)
    return parser


def wav_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = raw.resolve()
        if path.is_file():
            if path.suffix.casefold() != ".wav":
                raise ValueError(f"not a WAV file: {raw}")
            files.append(path)
        elif path.is_dir():
            files.extend(
                candidate.resolve()
                for candidate in path.rglob("*.wav")
                if candidate.is_file()
            )
        else:
            raise ValueError(f"path not found: {raw}")
    return sorted(dict.fromkeys(files))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.development or not args.holdout:
        raise SystemExit("provide at least one --development and one --holdout")

    try:
        development = wav_files(args.development)
        holdout = wav_files(args.holdout)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    if not development or not holdout:
        raise SystemExit("development and holdout sets must both contain WAV files")

    development_paths = {path.resolve() for path in development}
    holdout_paths = {path.resolve() for path in holdout}
    path_collisions = sorted(development_paths & holdout_paths)

    development_hashes: dict[str, list[Path]] = {}
    holdout_hashes: dict[str, list[Path]] = {}
    for path in development:
        development_hashes.setdefault(sha256(path), []).append(path)
    for path in holdout:
        holdout_hashes.setdefault(sha256(path), []).append(path)

    shared_hashes = sorted(
        set(development_hashes).intersection(holdout_hashes)
    )
    hash_collisions = [
        {
            "sha256": digest,
            "development": [
                str(path) for path in development_hashes[digest]
            ],
            "holdout": [str(path) for path in holdout_hashes[digest]],
        }
        for digest in shared_hashes
    ]

    payload = {
        "development_wavs": len(development),
        "holdout_wavs": len(holdout),
        "path_collisions": [str(path) for path in path_collisions],
        "hash_collisions": hash_collisions,
        "isolated": not path_collisions and not hash_collisions,
    }

    print(
        "wake corpus isolation "
        f"development={len(development)} holdout={len(holdout)} "
        f"path_collisions={len(path_collisions)} "
        f"hash_collisions={len(hash_collisions)} "
        f"result={'PASS' if payload['isolated'] else 'FAIL'}"
    )
    for collision in hash_collisions:
        print(
            "SHA COLLISION "
            f"{collision['sha256']} "
            f"development={collision['development']} "
            f"holdout={collision['holdout']}"
        )

    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return 0 if payload["isolated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
