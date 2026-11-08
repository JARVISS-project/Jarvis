"""Offline-only, process-isolated benchmark of the ten corrected 23W WAVs."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import wave
from array import array
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/voice-diagnostics"
INPUT = DATA / "sprint_23w_analysis.json"
OUTPUT = DATA / "sprint_23x_benchmark.json"
TRUTH = "Que horas são?"
SNAPSHOTS = {
    "tiny": "models--Systran--faster-whisper-tiny",
    "small": "models--Systran--faster-whisper-small",
    "medium": "models--Systran--faster-whisper-medium",
    "large-v3": "models--Systran--faster-whisper-large-v3",
    "large-v3-turbo": "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
}
CONFIGS = {
    "live_medium": {"model": "medium", "language": "pt", "beam": 1, "temperature": None, "vad": True},
    "explicit_pt_control": {"model": "medium", "language": "pt", "beam": 1, "temperature": None, "vad": True},
    "temperature_zero": {"model": "medium", "language": "pt", "beam": 1, "temperature": 0.0, "vad": True},
    "beam_five": {"model": "medium", "language": "pt", "beam": 5, "temperature": None, "vad": True},
    "vad_off": {"model": "medium", "language": "pt", "beam": 1, "temperature": None, "vad": False},
    "language_auto": {"model": "medium", "language": None, "beam": 1, "temperature": None, "vad": True},
    "tiny_model": {"model": "tiny", "language": "pt", "beam": 1, "temperature": None, "vad": True},
    "small_model": {"model": "small", "language": "pt", "beam": 1, "temperature": None, "vad": True},
    "large_v3_model": {"model": "large-v3", "language": "pt", "beam": 1, "temperature": None, "vad": True},
    "turbo_model": {"model": "large-v3-turbo", "language": "pt", "beam": 1, "temperature": None, "vad": True},
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFC", value.casefold())
    value = "".join(char if char.isalnum() or char.isspace() else " " for char in value)
    return " ".join(value.split())


def edit_distance(left: list[str] | str, right: list[str] | str) -> int:
    previous = list(range(len(right) + 1))
    for index, item in enumerate(left, start=1):
        current = [index]
        for column, other in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (item != other)))
        previous = current
    return previous[-1]


def scores(text: str) -> tuple[float, float]:
    reference = normalize(TRUTH)
    hypothesis = normalize(text)
    return (
        edit_distance(reference.split(), hypothesis.split()) / len(reference.split()),
        edit_distance(reference, hypothesis) / len(reference),
    )


def corpus() -> list[dict[str, object]]:
    attempts = json.loads(INPUT.read_text(encoding="utf-8"))["attempts"]
    if len(attempts) != 10 or any(item.get("ground_truth") != TRUTH for item in attempts):
        raise RuntimeError("Corrected ten-WAV ground truth is required")
    for item in attempts:
        path = Path(item["wav"]["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["wav"]["sha256"]:
            raise RuntimeError(f"WAV hash mismatch: {path}")
    return attempts


def snapshot(model: str) -> Path | None:
    folder = Path.home() / ".cache/huggingface/hub" / SNAPSHOTS[model] / "snapshots"
    if not folder.is_dir():
        return None
    for candidate in sorted(folder.iterdir()):
        if all((candidate / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
            return candidate
    return None


def read_pcm(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise ValueError(f"Invalid PCM WAV: {path}")
        pcm = array("h", wav.readframes(wav.getnframes()))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tolist()


def run_worker(config_name: str, run: int, output: Path) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["PYTHONPATH"] = str(ROOT / "src")
    sys.path.insert(0, str(ROOT / "src"))
    from jarvis.config import JarvisConfig
    from jarvis.context.recent import RecentContext
    from jarvis.speech.context import SpeechContextBuilder
    from jarvis.speech.nvidia_dlls import register_nvidia_dll_directories
    import numpy as np
    from faster_whisper import WhisperModel

    config = CONFIGS[config_name]
    local = snapshot(str(config["model"]))
    payload: dict[str, object] = {
        "config": config_name,
        "run": run,
        "matrix": config,
        "model_snapshot": str(local) if local else None,
        "rows": [],
        "error": None,
    }
    if local is None:
        payload["error"] = "model_snapshot_incomplete_or_absent"
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    production = JarvisConfig()
    hotwords = SpeechContextBuilder(RecentContext()).build_hotwords() if production.stt_hotwords_enabled else None
    payload["effective_options"] = {
        "device": "cuda", "compute_type": "float16",
        "task": "transcribe", "beam_size": config["beam"],
        "language": config["language"], "initial_prompt": production.stt_initial_prompt,
        "hotwords": hotwords, "vad_filter": config["vad"],
        "vad_parameters": {"min_silence_duration_ms": production.stt_vad_min_silence_duration_ms,
                           "speech_pad_ms": production.stt_vad_speech_pad_ms,
                           "threshold": production.stt_vad_threshold},
        "condition_on_previous_text": False,
        "temperature": config["temperature"] if config["temperature"] is not None else [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "suppress_tokens": [-1],
    }
    register_nvidia_dll_directories()
    model = None
    try:
        model = WhisperModel(str(local), device="cuda", compute_type="float16")
        for index, item in enumerate(corpus(), start=1):
            path = Path(item["wav"]["path"])
            audio = np.asarray(read_pcm(path), dtype=np.float32) / 32768.0
            kwargs = {
                "language": config["language"], "task": "transcribe",
                "beam_size": config["beam"], "condition_on_previous_text": False,
                "initial_prompt": production.stt_initial_prompt,
                "hotwords": hotwords,
                "vad_filter": config["vad"],
                "vad_parameters": payload["effective_options"]["vad_parameters"],
                "suppress_tokens": [-1],
            }
            if config["temperature"] is not None:
                kwargs["temperature"] = config["temperature"]
            started = time.perf_counter()
            row: dict[str, object] = {"attempt": index, "wav": str(path),
                                      "wav_sha256": item["wav"]["sha256"]}
            try:
                segment_iter, info = model.transcribe(audio, **kwargs)
                segments = list(segment_iter)
                text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
                wer, cer = scores(text)
                row.update({
                    "text": text, "normalized": normalize(text), "wer": wer, "cer": cer,
                    "language": getattr(info, "language", None),
                    "language_probability": getattr(info, "language_probability", None),
                    "duration_after_vad_seconds": getattr(info, "duration_after_vad", None),
                    "segments": [
                        {"text": segment.text, "avg_logprob": getattr(segment, "avg_logprob", None),
                         "no_speech_prob": getattr(segment, "no_speech_prob", None),
                         "compression_ratio": getattr(segment, "compression_ratio", None),
                         "temperature": getattr(segment, "temperature", None)}
                        for segment in segments
                    ],
                })
            except Exception as error:
                row["error"] = f"{type(error).__name__}: {error}"
            row["elapsed_seconds"] = time.perf_counter() - started
            payload["rows"].append(row)
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{config_name} run={run} {index}/10: {row.get('text', row.get('error'))}", flush=True)
    except Exception as error:
        payload["error"] = f"{type(error).__name__}: {error}"
    finally:
        del model
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def orchestrate(configs: list[str], *, repeats: int = 1) -> None:
    corpus()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {"ground_truth": TRUTH, "runs": []}
    for config_name in configs:
        for run in range(1, repeats + 1):
            if any(item["config"] == config_name and item["run"] == run for item in existing["runs"]):
                continue
            temporary = DATA / f"sprint_23x_{config_name}_run{run}.json"
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", config_name,
                       "--run", str(run), "--output", str(temporary)]
            completed = subprocess.run(command, cwd=ROOT, env={**os.environ,
                "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=900)
            if temporary.exists():
                result = json.loads(temporary.read_text(encoding="utf-8"))
            else:
                result = {"config": config_name, "run": run, "rows": [],
                          "error": f"worker_exit={completed.returncode}: {completed.stderr[-1500:]}"}
            result["worker_exit_code"] = completed.returncode
            result["worker_stdout_tail"] = completed.stdout[-800:]
            result["worker_stderr_tail"] = completed.stderr[-1500:]
            existing["runs"].append(result)
            OUTPUT.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{config_name} run={run}: {len(result['rows'])} rows; {result.get('error')}", flush=True)
    # Recheck every original after all subprocesses have exited.
    corpus()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=CONFIGS)
    parser.add_argument("--run", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--configs", nargs="*", choices=CONFIGS)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    if args.worker:
        if args.output is None:
            parser.error("--output required for worker")
        run_worker(args.worker, args.run, args.output)
    else:
        orchestrate(args.configs or list(CONFIGS), repeats=args.repeats)


if __name__ == "__main__":
    main()
