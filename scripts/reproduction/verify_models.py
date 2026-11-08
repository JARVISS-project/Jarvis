"""Verify explicitly supplied local model artifacts; never download or replace."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_files(root: Path, expected: dict[str, str]) -> dict:
    results = {}
    for name, digest in expected.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe manifest relative file")
        path = root / relative
        if not path.is_file():
            results[name] = {"status": "missing"}
        else:
            actual = sha256(path)
            results[name] = {"status": "match" if actual == digest else "mismatch",
                             "sha256": actual, "bytes": path.stat().st_size}
    return results


def verify_ollama(models: list[dict], expected: dict) -> dict:
    model = next((x for x in models if x.get("name") == expected["tag"]), None)
    if model is None:
        return {"status": "missing"}
    digest = str(model.get("digest", "")).removeprefix("sha256:")
    return {"status": "match" if digest == expected["manifest_sha256"] else "mismatch",
            "digest": digest, "tag": expected["tag"],
            "limit": "host API digest, not independent provisioning or layer reread"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--stt-dir", type=Path)
    parser.add_argument("--wake-dir", type=Path)
    parser.add_argument("--ollama", action="store_true")
    parser.add_argument("--settings", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = {}
    if args.settings:
        from jarvis.config import JarvisConfig
        config = JarvisConfig(settings_path=args.settings)
        selected = {"stt": config.stt_model_normal, "wake": config.wake_word_model,
                    "llm": config.model_name}
        report["selection"] = verify_selection(selected, manifest)
    if args.stt_dir:
        report["stt"] = verify_files(args.stt_dir, manifest["stt"]["files"])
    if args.wake_dir:
        report["wake"] = verify_files(args.wake_dir, manifest["wake"]["files"])
    if args.ollama:
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
                models = json.load(response)["models"]
            report["llm"] = verify_ollama(models, manifest["qwen"])
        except (OSError, ValueError, KeyError):
            report["llm"] = {"status": "unavailable"}
    if not report:
        parser.error("Explicit local artifacts or --ollama required")
    print(json.dumps(report))
    statuses = [
        row["status"] for group in ("stt", "wake")
        for row in report.get(group, {}).values()
    ]
    if "llm" in report:
        statuses.append(report["llm"]["status"])
    if "selection" in report:
        statuses.append(report["selection"]["status"])
    return int(any(status != "match" for status in statuses))


def verify_selection(selected: dict, manifest: dict) -> dict:
    expected = {"stt": manifest["stt"]["model_identifier"],
                "wake": manifest["wake"]["model_identifier"],
                "llm": manifest["qwen"]["tag"]}
    return {"status": "match" if selected == expected else "mismatch",
            "selected": selected, "expected": expected,
            "limit": "normal profile identifiers only; explicit files/revision must also match"}


if __name__ == "__main__":
    raise SystemExit(main())
