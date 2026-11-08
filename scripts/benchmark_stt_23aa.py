"""Process-isolated offline STT comparison on the frozen 23AA corpus."""

from __future__ import annotations

from array import array
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data/voice-diagnostics"
MANIFEST = Path(os.environ.get("JARVIS_STT_BENCH_MANIFEST", DATA / "sprint_23aa_corpus/manifest.json"))
OUTPUT = Path(os.environ.get("JARVIS_STT_BENCH_OUTPUT", DATA / "sprint_23aa_benchmark.json"))
EXPECTED_ROWS = int(os.environ.get("JARVIS_STT_BENCH_ROWS", "26"))
EXPECTED_SPLITS = set(os.environ.get("JARVIS_STT_BENCH_SPLITS", "development,evaluation").split(","))
MODELS = ("medium", "large-v3", "ptbr_ctc")


def corpus() -> list[dict]:
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))["rows"]
    if len(rows) != EXPECTED_ROWS or {row["split"] for row in rows} != EXPECTED_SPLITS:
        raise RuntimeError("Unexpected frozen corpus size or splits")
    for row in rows:
        path = Path(row["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise RuntimeError(f"Audio hash mismatch: {row['id']}")
    return rows


def read_pcm(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise RuntimeError(f"Unexpected WAV format: {path}")
        samples = array("h", source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tolist()


def metrics(reference: str, hypothesis: str) -> tuple[float, float, bool]:
    from scripts.benchmark_stt_23x import edit_distance, normalize
    ref, hyp = normalize(reference), normalize(hypothesis)
    return (
        edit_distance(ref.split(), hyp.split()) / len(ref.split()),
        edit_distance(ref, hyp) / len(ref),
        ref == hyp,
    )


def gpu_used_mib() -> int | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return int(result.stdout.strip().splitlines()[0].split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def worker(model_name: str, run: int, target: Path) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    sys.path.insert(0, str(ROOT / "src"))
    from scripts.benchmark_stt_23x import normalize, snapshot
    import numpy as np

    rows = corpus()
    payload: dict = {"model": model_name, "run": run, "offline": True,
                     "rows": [], "load_seconds": None, "error": None}
    model = None
    try:
        started = time.perf_counter()
        if model_name == "ptbr_ctc":
            from jarvis.speech.ptbr_fallback_worker import _confidence
            import torch
            from transformers import AutoModelForCTC, AutoProcessor
            model_manifest = json.loads((DATA / "sprint_23z_model_manifest.json").read_text(encoding="utf-8"))
            local = Path(model_manifest["snapshot"])
            for item in model_manifest["files"]:
                if hashlib.sha256((local / item["name"]).read_bytes()).hexdigest() != item["sha256"]:
                    raise RuntimeError(f"Fallback model hash mismatch: {item['name']}")
            processor = AutoProcessor.from_pretrained(str(local), local_files_only=True,
                                                      trust_remote_code=False)
            model = AutoModelForCTC.from_pretrained(str(local), local_files_only=True,
                                                    trust_remote_code=False, use_safetensors=True)
            model = model.to("cuda").eval()
            blank_id = int(getattr(model.config, "pad_token_id", 0) or 0)
            payload["model_snapshot"] = str(local)
        else:
            from faster_whisper import WhisperModel
            from jarvis.config import JarvisConfig
            from jarvis.context.recent import RecentContext
            from jarvis.speech.context import SpeechContextBuilder
            from jarvis.speech.nvidia_dlls import register_nvidia_dll_directories
            production = JarvisConfig()
            hotwords = (SpeechContextBuilder(RecentContext()).build_hotwords()
                        if production.stt_hotwords_enabled else None)
            local = snapshot(model_name)
            if local is None:
                raise RuntimeError(f"Local Whisper snapshot absent: {model_name}")
            register_nvidia_dll_directories()
            model = WhisperModel(str(local), device="cuda", compute_type="float16")
            payload["model_snapshot"] = str(local)
            payload["options"] = {
                "language": "pt", "task": "transcribe", "beam_size": 1,
                "condition_on_previous_text": False, "vad_filter": True,
                "temperature": [0, .2, .4, .6, .8, 1],
                "initial_prompt": production.stt_initial_prompt,
                "hotwords": hotwords,
                "vad_parameters": {
                    "min_silence_duration_ms": production.stt_vad_min_silence_duration_ms,
                    "speech_pad_ms": production.stt_vad_speech_pad_ms,
                    "threshold": production.stt_vad_threshold,
                },
                "suppress_tokens": [-1],
            }
        payload["load_seconds"] = time.perf_counter() - started
        for item in rows:
            row = {"id": item["id"], "split": item["split"],
                   "sha256": item["sha256"], "reference": item["reference"]}
            started = time.perf_counter()
            try:
                pcm = read_pcm(Path(item["path"]))
                audio = np.asarray(pcm, dtype=np.float32) / 32768.0
                if model_name == "ptbr_ctc":
                    inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
                    values = inputs.input_values.to("cuda")
                    mask = getattr(inputs, "attention_mask", None)
                    kwargs = {"attention_mask": mask.to("cuda")} if mask is not None else {}
                    with torch.inference_mode():
                        logits = model(values, **kwargs).logits
                    ids = torch.argmax(logits, dim=-1)
                    text = processor.batch_decode(ids)[0].strip()
                    confidence, margin = _confidence(logits, torch, blank_id)
                    row.update({"confidence": confidence, "margin": margin,
                                "gate_accepts": bool(text and confidence >= .96 and margin >= 6.5)})
                    torch.cuda.synchronize()
                    row["torch_peak_vram_bytes"] = torch.cuda.max_memory_allocated()
                else:
                    options = payload["options"]
                    iterator, info = model.transcribe(audio, **options)
                    segments = list(iterator)
                    text = " ".join(segment.text.strip() for segment in segments
                                    if segment.text.strip())
                    row["language"] = info.language
                wer, cer, exact = metrics(item["reference"], text)
                row.update({"text": text, "normalized": normalize(text),
                            "wer": wer, "cer": cer, "exact": exact})
            except Exception as error:
                row["error"] = f"{type(error).__name__}: {error}"
            row["elapsed_seconds"] = time.perf_counter() - started
            payload["rows"].append(row)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    except Exception as error:
        payload["error"] = f"{type(error).__name__}: {error}"
    finally:
        del model
        gc.collect()
        if model_name == "ptbr_ctc":
            try:
                torch.cuda.empty_cache()
            except (NameError, RuntimeError):
                pass
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def orchestrate() -> None:
    corpus()
    results = []
    for model_name in MODELS:
        for run in range(1, 4):
            temporary = OUTPUT.with_name(f"{OUTPUT.stem}_{model_name}_run{run}.json")
            command = [sys.executable, str(Path(__file__).resolve()), "--worker",
                       model_name, str(run), str(temporary)]
            baseline = gpu_used_mib()
            peaks = [baseline] if baseline is not None else []
            stop = Event()

            def sample() -> None:
                while not stop.wait(.20):
                    current = gpu_used_mib()
                    if current is not None:
                        peaks.append(current)

            monitor = Thread(target=sample, daemon=True)
            monitor.start()
            try:
                completed = subprocess.run(
                    command, cwd=ROOT,
                    env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=900,
                )
            finally:
                stop.set()
                monitor.join(timeout=6)
            result = (json.loads(temporary.read_text(encoding="utf-8"))
                      if temporary.exists() else {"model": model_name, "run": run,
                                                  "rows": [], "error": "no_worker_output"})
            result["exit_code"] = completed.returncode
            result["stderr_tail"] = completed.stderr[-1000:]
            result["device_vram_baseline_mib"] = baseline
            result["device_vram_peak_mib"] = max(peaks) if peaks else None
            result["device_vram_delta_mib"] = ((max(peaks) - baseline)
                                                if peaks and baseline is not None else None)
            results.append(result)
            OUTPUT.write_text(json.dumps({"runs": results}, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            print(f"{model_name} run={run} rows={len(result['rows'])} error={result['error']}",
                  flush=True)
    corpus()


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        worker(sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]))
    else:
        orchestrate()
