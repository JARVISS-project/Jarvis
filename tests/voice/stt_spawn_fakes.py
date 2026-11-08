"""Spawn-safe STT fake: no pytest, pipeline, GPU or provider imports."""
import os
from threading import Event

from jarvis.speech.base import TranscriptionResult
from jarvis.speech.stt_process import SttModelConfig


class SpawnFakeStt:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def transcribe(self, audio, sample_rate):
        if self.mode == "hang":
            Event().wait()
        if self.mode == "crash":
            os._exit(17)
        if self.mode == "oom":
            raise RuntimeError("CUDA out of memory: secret")
        return TranscriptionResult("candidato", "pt", 1.)

    def close(self) -> None:
        pass


def spawn_fake_factory(config: SttModelConfig):
    if config.model_name == "load_hang":
        Event().wait()
    if config.model_name == "load_crash":
        os._exit(18)
    return SpawnFakeStt(config.model_name)


def spawn_cpu_load(stop):
    """Controlled test-owned CPU work; never imports a model or touches GPU."""
    while not stop.is_set():
        sum(index * index for index in range(20000))
