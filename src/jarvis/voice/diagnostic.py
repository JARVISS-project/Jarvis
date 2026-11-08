"""Opt-in, local-only trace for a bounded voice diagnostic session."""

from __future__ import annotations

import hashlib
import json
import sys
import wave
from array import array
from datetime import datetime
from pathlib import Path


class VoiceDiagnosticRecorder:
    def __init__(
        self,
        path: Path,
        *,
        wav_directory: Path | None = None,
        wav_limit: int = 0,
    ) -> None:
        if wav_limit < 0:
            raise ValueError("wav_limit must be nonnegative")
        self.path = path
        self.wav_directory = wav_directory
        self.wav_limit = wav_limit
        self.turn_number = 0
        self.capture_number = 0
        self.current_turn_id: str | None = None
        self.current_capture_id: str | None = None
        self.saved_wavs = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        if wav_directory is not None:
            wav_directory.mkdir(parents=True, exist_ok=True)

    def new_turn(self) -> str:
        self.turn_number += 1
        self.current_turn_id = f"turn-{self.turn_number:02d}"
        self.current_capture_id = None
        return self.current_turn_id

    def new_capture(self) -> str:
        self.capture_number += 1
        self.current_capture_id = f"capture-{self.capture_number:02d}"
        return self.current_capture_id

    def record(self, event: str, **fields: object) -> None:
        entry = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "turn_id": self.current_turn_id,
            "capture_id": self.current_capture_id,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save_wav(
        self, audio: list[int], sample_rate: int, *, source: str = "stt_input"
    ) -> None:
        if self.wav_directory is None or self.saved_wavs >= self.wav_limit:
            return
        self.saved_wavs += 1
        path = self.wav_directory / f"attempt_{self.saved_wavs:02d}.wav"
        payload = array("h", audio)
        if payload.itemsize != 2:
            raise RuntimeError("Expected 16-bit PCM")
        if sys.byteorder != "little":
            payload.byteswap()
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(payload.tobytes())
        self.record(
            "capture_wav",
            path=str(path),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            frames=len(audio),
            source=source,
        )
