from __future__ import annotations

from array import array
from dataclasses import dataclass
from collections.abc import Sequence
import base64
import json
import queue
import subprocess
import sys
from threading import Lock, Thread
from typing import Protocol


@dataclass(frozen=True)
class PtBrFallbackResult:
    text: str
    confidence: float
    margin: float
    duration_seconds: float


class PtBrFallback(Protocol):
    def transcribe(
        self,
        audio: Sequence[int],
        sample_rate: int,
    ) -> PtBrFallbackResult | None: ...

    def accepts(self, result: PtBrFallbackResult) -> bool: ...

    def close(self) -> None: ...


class PtBrFallbackClient:
    """Lazy isolated pt-BR CTC fallback worker.

    The worker lives in a separate Python process because PyTorch and
    CTranslate2 can load incompatible cuDNN DLLs when hosted together on
    Windows. Audio never leaves the machine.
    """

    def __init__(
        self,
        *,
        model_name: str = (
            "lgris/wav2vec2-large-xlsr-open-brazilian-portuguese-v2"
        ),
        minimum_confidence: float = 0.96,
        minimum_margin: float = 6.5,
        startup_timeout_seconds: float = 45.0,
        response_timeout_seconds: float = 10.0,
    ) -> None:
        self.model_name = model_name
        self.minimum_confidence = minimum_confidence
        self.minimum_margin = minimum_margin
        self.startup_timeout_seconds = startup_timeout_seconds
        self.response_timeout_seconds = response_timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, object]] = queue.Queue()
        self._disabled = False
        self._start_lock = Lock()

    def warm_up_async(self) -> None:
        if self._disabled:
            return
        Thread(target=self._ensure_worker, daemon=True).start()

    def accepts(self, result: PtBrFallbackResult) -> bool:
        return (
            bool(result.text.strip())
            and result.confidence >= self.minimum_confidence
            and result.margin >= self.minimum_margin
        )

    def transcribe(
        self,
        audio: Sequence[int],
        sample_rate: int,
    ) -> PtBrFallbackResult | None:
        if self._disabled or sample_rate != 16000 or not audio:
            return None
        if not self._ensure_worker():
            return None

        process = self._process
        if process is None or process.stdin is None:
            return None
        samples = array("h", (max(-32768, min(32767, int(x))) for x in audio))
        payload = {
            "type": "transcribe",
            "sample_rate": sample_rate,
            "audio_b64": base64.b64encode(samples.tobytes()).decode("ascii"),
        }
        try:
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()
            message = self._messages.get(
                timeout=self.response_timeout_seconds
            )
        except (BrokenPipeError, OSError, queue.Empty):
            self._disable()
            return None

        if message.get("type") != "result":
            return None
        try:
            return PtBrFallbackResult(
                text=str(message.get("text", "")).strip(),
                confidence=float(message.get("confidence", 0.0)),
                margin=float(message.get("margin", 0.0)),
                duration_seconds=float(message.get("duration_seconds", 0.0)),
            )
        except (TypeError, ValueError):
            return None

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write('{"type":"close"}\n')
                process.stdin.flush()
                process.wait(timeout=1.0)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.terminate()
        if process.poll() is None:
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()

    def _ensure_worker(self) -> bool:
        if getattr(sys, 'frozen', False):
            # Optional Torch backend is not shipped; never launch Python -m
            # against a windowed frozen entry or open another UI by accident.
            self._disable()
            return False
        with self._start_lock:
            process = self._process
            if process is not None and process.poll() is None:
                return True

            self.close()
            while True:
                try:
                    self._messages.get_nowait()
                except queue.Empty:
                    break
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        "-m",
                        "jarvis.speech.ptbr_fallback_worker",
                        "--model",
                        self.model_name,
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except OSError:
                self._disable()
                return False

            self._process = process
            Thread(
                target=self._read_worker_output,
                args=(process,),
                daemon=True,
            ).start()
            try:
                message = self._messages.get(
                    timeout=self.startup_timeout_seconds
                )
            except queue.Empty:
                self._disable()
                return False
            if message.get("type") != "ready":
                self._disable()
                return False
            return True

    def _read_worker_output(self, process: subprocess.Popen[str]) -> None:
        stdout = process.stdout
        if stdout is None:
            return
        for line in stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self._messages.put(message)

    def _disable(self) -> None:
        self._disabled = True
        self.close()
