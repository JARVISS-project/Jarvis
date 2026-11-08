"""Reproducible 23AH-thread versus 23AI-process STT audit."""
from __future__ import annotations

from array import array
from dataclasses import asdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from threading import Thread
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
DATA = ROOT / "data/voice-diagnostics"
MANIFEST = DATA / "sprint_23ah_manifest.json"
RAW = DATA / "sprint_23aj_audit"
SUMMARY = ROOT / "docs/sprint-23aj-equivalence.json"
MODES = ("thread", "process")
RUNS = 3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _pcm(path: Path) -> tuple[list[int], dict[str, object]]:
    with wave.open(str(path), "rb") as source:
        details = {
            "sample_rate": source.getframerate(),
            "channels": source.getnchannels(),
            "sample_width": source.getsampwidth(),
            "frames": source.getnframes(),
        }
        samples = array("h", source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tolist(), details


def _environment() -> dict[str, object]:
    import ctranslate2
    import faster_whisper
    import numpy
    from faster_whisper import WhisperModel
    from scripts.benchmark_stt_23x import snapshot

    model = snapshot("large-v3")
    if model is None:
        raise RuntimeError("large-v3 snapshot is unavailable")
    relevant = ("CUDA", "CUDNN", "CT2", "OMP", "MKL", "HF_", "TRANSFORMERS")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "faster_whisper": faster_whisper.__version__,
        "ctranslate2": ctranslate2.__version__,
        "numpy": numpy.__version__,
        "cuda_devices": ctranslate2.get_cuda_device_count(),
        "cuda_compute_types": sorted(ctranslate2.get_supported_compute_types("cuda")),
        "model_snapshot": str(model),
        "model_revision": model.name,
        "model_files": {
            str(path.relative_to(model)): {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in sorted(model.rglob("*")) if path.is_file()
        },
        "transcribe_signature": str(inspect.signature(WhisperModel.transcribe)),
        "environment": {key: value for key, value in sorted(os.environ.items())
                        if key.startswith(relevant)},
        "seed": None,
        "seed_reason": "beam decoding starts at temperature 0; no seed API is exposed",
    }


def _options() -> dict[str, object]:
    from jarvis.config import JarvisConfig
    config = JarvisConfig()
    return {
        "language": config.stt_language,
        "task": "transcribe",
        "beam_size": 1,
        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "condition_on_previous_text": False,
        "initial_prompt": None,
        "hotwords": None,
        "suppress_tokens": [-1],
        "without_timestamps": False,
        "vad_filter": config.stt_vad_enabled,
        "vad_parameters": {
            "min_silence_duration_ms": config.stt_vad_min_silence_duration_ms,
            "speech_pad_ms": config.stt_vad_speech_pad_ms,
            "threshold": config.stt_vad_threshold,
        },
        "device": "cuda",
        "compute_type": "float16",
        "pcm_dtype": "int16_le",
        "model_input_dtype": "float32",
        "normalization": "numpy.asarray(pcm, float32) / 32768.0",
        "reset_between_files": "condition_on_previous_text=False; model reused",
    }


def _execute(rows: list[dict], mode: str, run: int, target: Path) -> None:
    from faster_whisper import WhisperModel
    from jarvis.speech.faster_whisper import FasterWhisperSTT
    from jarvis.voice.semantic_shadow import parse_text
    from scripts.benchmark_stt_23aa import metrics
    from scripts.benchmark_stt_23x import normalize, snapshot

    options = _options()
    environment = _environment()
    result: dict[str, object] = {
        "mode": mode, "run": run, "environment": environment,
        "options": options, "order": [row["id"] for row in rows], "rows": [],
    }
    model_path = snapshot("large-v3")
    assert model_path is not None
    captures: list[tuple[list[object], object, dict[str, object]]] = []

    class CapturingModel:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.model = WhisperModel(*args, **kwargs)

        def transcribe(self, audio: object, **kwargs: object):
            iterator, info = self.model.transcribe(audio, **kwargs)
            segments = list(iterator)
            captures.append((segments, info, dict(kwargs)))
            return iter(segments), info

    stt = FasterWhisperSTT(
        model_name="large-v3", device="cuda", compute_type="float16",
        language=str(options["language"]), model_factory=CapturingModel,
        allow_device_fallback=False, vad_enabled=bool(options["vad_filter"]),
        vad_min_silence_duration_ms=int(options["vad_parameters"]["min_silence_duration_ms"]),
        vad_speech_pad_ms=int(options["vad_parameters"]["speech_pad_ms"]),
        vad_threshold=float(options["vad_parameters"]["threshold"]),
    )
    for item in rows:
        path = Path(item["path"])
        pcm, audio = _pcm(path)
        transcription = stt.transcribe(pcm, int(audio["sample_rate"]))
        segments, info, forwarded = captures.pop(0)
        text = transcription.text
        wer, cer, exact = metrics(item["reference"], text)
        result["rows"].append({
            "id": item["id"], "path_name": path.name,
            "wav_sha256": _sha256(path), "manifest_sha256": item["sha256"],
            "audio": audio, "reference": item["reference"], "raw_text": text,
            "forwarded_kwargs": forwarded,
            "normalized": normalize(text), "wer": wer, "cer": cer, "exact": exact,
            "intent_slots": asdict(parse_text(text)),
            "segments": [{
                "id": segment.id, "seek": segment.seek,
                "start": segment.start, "end": segment.end,
                "text": segment.text, "tokens": list(segment.tokens),
                "temperature": segment.temperature,
                "avg_logprob": segment.avg_logprob,
                "compression_ratio": segment.compression_ratio,
                "no_speech_prob": segment.no_speech_prob,
            } for segment in segments],
            "metadata": {
                "language": info.language,
                "language_probability": info.language_probability,
                "duration": info.duration,
                "duration_after_vad": info.duration_after_vad,
            },
        })
    stt.close()
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")


def _worker(mode: str, run: int, target: Path) -> int:
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))["rows"]
    if mode == "thread":
        errors: list[BaseException] = []
        def run_thread() -> None:
            try:
                _execute(rows, mode, run, target)
            except BaseException as error:
                errors.append(error)
        thread = Thread(target=run_thread, name="stt-candidate-shadow")
        thread.start(); thread.join()
        if errors:
            raise errors[0]
    else:
        _execute(rows, mode, run, target)
    return 0


def _compare(payloads: list[dict]) -> dict[str, object]:
    baseline = payloads[0]
    comparisons = []
    for index, expected in enumerate(baseline["rows"]):
        variants = [payload["rows"][index] for payload in payloads]
        texts = [row["raw_text"] for row in variants]
        segments = [row["segments"] for row in variants]
        comparisons.append({
            "id": expected["id"], "path_name": expected["path_name"],
            "wav_sha256": expected["wav_sha256"],
            "reference": expected["reference"],
            "raw_text": expected["raw_text"],
            "thread_texts": texts[:RUNS], "process_texts": texts[RUNS:],
            "all_text_equal": len(set(texts)) == 1,
            "all_segments_equal": all(value == segments[0] for value in segments[1:]),
            "normalized": expected["normalized"], "wer": expected["wer"],
            "cer": expected["cer"], "intent_slots": expected["intent_slots"],
            "segments": expected["segments"], "metadata": expected["metadata"],
            "forwarded_kwargs": expected["forwarded_kwargs"],
        })
    return {
        "schema_version": 1,
        "mechanisms": [f"{payload['mode']}-{payload['run']}" for payload in payloads],
        "environment": baseline["environment"],
        "effective_options": baseline["options"],
        "file_order": baseline["order"],
        "environment_equal": all(payload["environment"] == baseline["environment"]
                                 for payload in payloads[1:]),
        "options_equal": all(payload["options"] == baseline["options"]
                             for payload in payloads[1:]),
        "order_equal": all(payload["order"] == baseline["order"]
                           for payload in payloads[1:]),
        "all_37_texts_equal": all(row["all_text_equal"] for row in comparisons),
        "all_37_segments_equal": all(row["all_segments_equal"] for row in comparisons),
        "aggregate": {
            "wer": sum(row["wer"] for row in comparisons) / len(comparisons),
            "cer": sum(row["cer"] for row in comparisons) / len(comparisons),
            "exact": sum(row["wer"] == 0 and row["cer"] == 0 for row in comparisons),
        },
        "rows": comparisons,
    }


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        return _worker(sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    RAW.mkdir(parents=True, exist_ok=True)
    payloads = []
    for mode in MODES:
        for run in range(1, RUNS + 1):
            target = RAW / f"{mode}-{run}.json"
            if "--summarize-existing" not in sys.argv:
                completed = subprocess.run(
                    [sys.executable, __file__, "--worker", mode, str(run), str(target)],
                    cwd=ROOT, env=os.environ, timeout=300, check=False,
                )
                if completed.returncode:
                    raise RuntimeError(f"{mode}-{run} failed: {completed.returncode}")
            payloads.append(json.loads(target.read_text(encoding="utf-8")))
    summary = _compare(payloads)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"},
                     ensure_ascii=True))
    return 0 if summary["all_37_texts_equal"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
