"""Bounded inventory of installed Piper pairs. No downloads or synthesis."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys


@dataclass(frozen=True)
class InstalledVoice:
    id: str
    label: str
    model: Path
    config: Path


def installed_voices(config) -> list[InstalledVoice]:
    root = Path('models/voices' if getattr(sys, 'frozen', False) else 'data/voices').resolve()
    current = config.tts_piper_model_path.resolve()
    candidates = [current]
    if root.is_dir():
        # Limit inventory and refuse symlink traversal outside the managed root.
        for model in root.rglob("*.onnx"):
            if len(candidates) >= 64:
                break
            if model.resolve().is_relative_to(root):
                candidates.append(model.resolve())
    voices = []
    for model in sorted(set(candidates)):
        metadata = (config.tts_piper_config_path.resolve() if model == current
                    else Path(str(model) + ".json"))
        try:
            if not model.is_file() or not metadata.is_file() or metadata.stat().st_size > 1_000_000:
                continue
            if model != current and not metadata.resolve().is_relative_to(root):
                continue
            data = json.loads(metadata.read_text(encoding="utf-8"))
            rate = data["audio"]["sample_rate"]
            language = data["language"]["code"]
            if (type(rate) is not int or not 8000 <= rate <= 96000
                    or not isinstance(language, str) or len(language) > 32
                    or not isinstance(data.get("phoneme_id_map"), dict)):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        identity = hashlib.sha256(str(model).encode("utf-8")).hexdigest()[:24]
        voices.append(InstalledVoice(identity, f"{model.stem} · {language}", model, metadata))
    return voices
