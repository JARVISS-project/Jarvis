"""Minimal import surface for the real spawn protocol tests."""
import os
from threading import Event

from jarvis.speech.base import TranscriptionResult
from jarvis.speech.stt_process import SttModelConfig


class SpawnModel:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def warm_up(self) -> None:
        if self.mode == "load_hang":
            Event().wait()
        if self.mode == "load_crash":
            os._exit(31)
        if self.mode == "load_oom":
            raise RuntimeError("CUDA out of memory secret")

    def transcribe(self, audio: object, sample_rate: int) -> TranscriptionResult:
        if self.mode == "hang":
            Event().wait()
        if self.mode == "crash":
            os._exit(32)
        if self.mode == "oom":
            raise RuntimeError("CUDA out of memory secret")
        return TranscriptionResult("principal", "pt", .25, -.1, .01, .99, .2)

    def transcribe_retry(self, audio: object, sample_rate: int) -> TranscriptionResult:
        return self.transcribe(audio, sample_rate)

    def close(self) -> None:
        pass


def spawn_factory(config: SttModelConfig) -> SpawnModel:
    return SpawnModel(config.model_name)
