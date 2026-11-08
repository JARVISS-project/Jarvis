"""Visible, UTF-8 diagnostic launcher for ten consented voice captures."""

from __future__ import annotations

import hashlib
import argparse
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jarvis.config import JarvisConfig  # noqa: E402
from jarvis.runtime.cli import main as runtime_main  # noqa: E402


class Utf8Tee(io.TextIOBase):
    def __init__(self, console: io.TextIOBase, path: Path) -> None:
        self.console = console
        self.file = path.open("w", encoding="utf-8", newline="\n")

    @property
    def encoding(self) -> str:
        return "utf-8"

    def write(self, value: str) -> int:
        self.console.write(value)
        self.file.write(value)
        self.flush()
        return len(value)

    def flush(self) -> None:
        self.console.flush()
        self.file.flush()

    def isatty(self) -> bool:
        return self.console.isatty()

    def fileno(self) -> int:
        return self.console.fileno()

    def close(self) -> None:
        self.file.close()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav-limit", type=int, default=10)
    args = parser.parse_args()
    if not 0 < args.wav_limit <= 10:
        parser.error("--wav-limit must be between 1 and 10")
    import jarvis
    import openwakeword

    package_path = Path(jarvis.__file__).resolve()
    if ROOT / "src/jarvis" not in package_path.parents:
        raise RuntimeError(f"Wrong checkout package: {package_path}")
    config = JarvisConfig()
    if (
        config.wake_word_verifier_enabled
        or config.wake_word_model != "hey_jarvis"
        or config.wake_word_threshold != 0.5
        or config.wake_min_consecutive_detections != 2
    ):
        raise RuntimeError("Sprint 23V wake configuration changed; refusing diagnostic")
    model_path = next(
        Path(path) for path in openwakeword.get_pretrained_model_paths("onnx")
        if Path(path).name == "hey_jarvis_v0.1.onnx"
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / "data/voice-diagnostics" / f"sprint_23w_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    (output / "wavs").mkdir()
    (output / "configuration.json").write_text(
        json.dumps({
            "started_at": datetime.now().astimezone().isoformat(),
            "package_path": str(package_path),
            "model_path": str(model_path),
            "model_sha256": sha256(model_path),
            "external_verifier": False,
            "npz_path_preserved": str(config.wake_word_verifier_path),
            "npz_sha256": sha256(config.wake_word_verifier_path),
            "wake_threshold": 0.5,
            "wake_consecutive_frames": 2,
            "stt_model": config.stt_model_normal,
            "stt_vad_enabled": config.stt_vad_enabled,
            "wav_limit": args.wav_limit,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.environ["JARVIS_VOICE_DIAGNOSTIC_PATH"] = str(output / "turns.jsonl")
    os.environ["JARVIS_VOICE_DIAGNOSTIC_WAV_DIR"] = str(output / "wavs")
    os.environ["JARVIS_VOICE_DIAGNOSTIC_WAV_LIMIT"] = str(args.wav_limit)
    os.environ["JARVIS_VOICE_DEBUG"] = "1"
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    original_stdout, original_stderr = sys.stdout, sys.stderr
    tee = Utf8Tee(original_stdout, output / "console.log")
    sys.stdout = tee
    sys.stderr = tee
    try:
        print(f"Diagnóstico 23W: {output}", flush=True)
        print(
            f"Aguardando JARVIS pronto; {args.wav_limit} capturas de comando no máximo.",
            flush=True,
        )
        runtime_main(["--voice"])
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        tee.close()


if __name__ == "__main__":
    main()
