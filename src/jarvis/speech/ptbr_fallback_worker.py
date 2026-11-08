from __future__ import annotations

from array import array
import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
import json
import math
import os
import sys


def _emit(payload: dict[str, object]) -> None:
    # Keep the worker protocol ASCII-only so Windows console/code-page
    # defaults cannot corrupt accented Portuguese when the parent decodes
    # the pipe as UTF-8. json.loads restores the original Unicode text.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _confidence(logits, torch, blank_id: int) -> tuple[float, float]:
    log_probs = torch.log_softmax(logits[0], dim=-1)
    top_values, top_ids = torch.topk(log_probs, k=2, dim=-1)
    predicted = top_ids[:, 0].tolist()

    runs: list[tuple[float, float]] = []
    index = 0
    while index < len(predicted):
        token = predicted[index]
        end = index + 1
        while end < len(predicted) and predicted[end] == token:
            end += 1
        if token != blank_id:
            local = top_values[index:end, 0]
            relative = int(torch.argmax(local).item())
            frame = index + relative
            probability = math.exp(float(top_values[frame, 0].item()))
            margin = float(
                (top_values[frame, 0] - top_values[frame, 1]).item()
            )
            runs.append((probability, margin))
        index = end

    if not runs:
        return 0.0, 0.0
    probabilities = [probability for probability, _margin in runs]
    margins = [margin for _probability, margin in runs]
    geometric_mean = math.exp(
        sum(math.log(max(value, 1e-12)) for value in probabilities)
        / len(probabilities)
    )
    return geometric_mean, sum(margins) / len(margins)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    try:
        with redirect_stdout(sys.stderr), redirect_stderr(sys.stderr):
            import numpy as np
            import torch
            from transformers import AutoModelForCTC, AutoProcessor
            from transformers.utils import logging as transformers_logging

            transformers_logging.set_verbosity_error()
            transformers_logging.disable_progress_bar()
            processor = AutoProcessor.from_pretrained(
                args.model,
                trust_remote_code=False,
                local_files_only=True,
            )
            model = AutoModelForCTC.from_pretrained(
                args.model,
                trust_remote_code=False,
                use_safetensors=True,
                local_files_only=True,
            )
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = model.to(device).eval()
    except Exception as error:
        _emit(
            {
                "type": "error",
                "error": type(error).__name__,
                "phase": "startup",
            }
        )
        return 2

    blank_id = int(getattr(model.config, "pad_token_id", 0) or 0)
    _emit({"type": "ready", "device": device})

    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _emit({"type": "error", "error": "invalid_json"})
            continue
        if not isinstance(request, dict):
            _emit({"type": "error", "error": "invalid_request"})
            continue
        if request.get("type") == "close":
            return 0
        if request.get("type") != "transcribe":
            _emit({"type": "error", "error": "unsupported_request"})
            continue

        try:
            sample_rate = int(request.get("sample_rate", 0))
            if sample_rate != 16000:
                raise ValueError("sample_rate")
            raw = base64.b64decode(
                str(request.get("audio_b64", "")),
                validate=True,
            )
            samples = array("h")
            samples.frombytes(raw)
            if sys.byteorder != "little":
                samples.byteswap()
            audio = np.asarray(samples, dtype=np.float32) / 32768.0
            if audio.size == 0:
                raise ValueError("empty_audio")

            inputs = processor(
                audio,
                sampling_rate=sample_rate,
                return_tensors="pt",
            )
            values = inputs.input_values.to(device)
            attention_mask = getattr(inputs, "attention_mask", None)
            kwargs = (
                {"attention_mask": attention_mask.to(device)}
                if attention_mask is not None
                else {}
            )
            with torch.inference_mode():
                logits = model(values, **kwargs).logits
            token_ids = torch.argmax(logits, dim=-1)
            text = processor.batch_decode(token_ids)[0].strip()
            confidence, margin = _confidence(logits, torch, blank_id)
            _emit(
                {
                    "type": "result",
                    "text": text,
                    "confidence": confidence,
                    "margin": margin,
                    "duration_seconds": float(audio.size / sample_rate),
                }
            )
        except Exception as error:
            _emit(
                {
                    "type": "error",
                    "error": type(error).__name__,
                    "phase": "transcribe",
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
