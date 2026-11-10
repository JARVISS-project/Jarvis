from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, signature
from multiprocessing import get_context
from multiprocessing.context import SpawnContext
from multiprocessing.process import BaseProcess
from pathlib import Path
from queue import Empty
from typing import Any
import signal
import traceback
import uuid


@dataclass(frozen=True)
class ChatterboxProcessConfig:
    device: str
    language_id: str
    reference_audio_path: Path | None
    t3_model: str
    exaggeration: float
    cfg_weight: float


class ChatterboxProcessError(RuntimeError):
    pass


class ChatterboxProcessInterrupted(RuntimeError):
    pass


def _supports_t3_model(loader: Any) -> bool:
    try:
        parameters = signature(loader).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "t3_model"
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _resolve_device(requested: str) -> str:
    normalized = requested.casefold().strip()
    if normalized != "auto":
        return normalized
    try:
        import torch
    except ImportError:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_model(device: str, t3_model: str) -> Any:
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    loader = ChatterboxMultilingualTTS.from_pretrained
    if _supports_t3_model(loader):
        return loader(device=device, t3_model=t3_model)
    return loader(device=device)


def _to_numpy(audio: Any) -> Any:
    import numpy as np

    if hasattr(audio, "detach"):
        audio = audio.detach()
    if hasattr(audio, "cpu"):
        audio = audio.cpu()
    if hasattr(audio, "float"):
        audio = audio.float()
    if hasattr(audio, "numpy"):
        audio = audio.numpy()
    return np.asarray(audio, dtype=np.float32)


def _worker_main(command_queue: Any, result_queue: Any, config: ChatterboxProcessConfig) -> None:
    # Ctrl+C belongs to the parent voice process. Spawned Windows children
    # otherwise receive the console interrupt too and print a noisy traceback
    # while blocked on Queue.get(). The parent closes this worker explicitly.
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (AttributeError, ValueError):
        pass

    model: Any | None = None
    device = _resolve_device(config.device)

    while True:
        command = command_queue.get()
        kind = command.get("kind")
        request_id = command.get("request_id")
        if kind == "shutdown":
            return
        try:
            if model is None:
                if config.reference_audio_path is not None:
                    from jarvis.tts.reference import load_local_model
                    model = load_local_model(device)
                else:
                    model = _load_model(device, config.t3_model)
            if kind == "warmup":
                result_queue.put(
                    {
                        "kind": "ok",
                        "request_id": request_id,
                        "device": device,
                    }
                )
                continue
            if kind != "generate":
                raise RuntimeError(f"Comando neural desconhecido: {kind!r}")

            kwargs: dict[str, Any] = {
                "language_id": config.language_id,
                "exaggeration": config.exaggeration,
                "cfg_weight": config.cfg_weight,
            }
            if config.reference_audio_path is not None:
                kwargs["audio_prompt_path"] = str(config.reference_audio_path)
            audio = model.generate(command["text"], **kwargs)
            result_queue.put(
                {
                    "kind": "audio",
                    "request_id": request_id,
                    "sample_rate": int(model.sr),
                    "audio": _to_numpy(audio),
                    "device": device,
                }
            )
        except BaseException as error:
            result_queue.put(
                {
                    "kind": "error",
                    "request_id": request_id,
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                }
            )


class ChatterboxProcessClient:
    """Runs Torch/Chatterbox in a spawned process to isolate CUDA DLLs.

    Faster-Whisper/CTranslate2 and PyTorch can load different cuDNN DLL sets on
    Windows. A spawned child gets its own DLL namespace and avoids whichever
    stack happened to load cuDNN first in the voice process.
    """

    def __init__(
        self,
        config: ChatterboxProcessConfig,
        *,
        context: SpawnContext | None = None,
    ) -> None:
        self.config = config
        self._context = context or get_context("spawn")
        self._command_queue: Any | None = None
        self._result_queue: Any | None = None
        self._process: BaseProcess | None = None
        self._cancelled = False

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def warm_up(self) -> str:
        result = self._request("warmup")
        return str(result.get("device", "unknown"))

    def generate(self, text: str) -> tuple[Any, int]:
        result = self._request("generate", text=text)
        return result["audio"], int(result["sample_rate"])

    def cancel(self) -> None:
        self._cancelled = True
        self._terminate_process()

    def close(self) -> None:
        process = self._process
        command_queue = self._command_queue
        if process is not None and process.is_alive() and command_queue is not None:
            try:
                command_queue.put({"kind": "shutdown", "request_id": uuid.uuid4().hex})
                process.join(timeout=1.0)
            except Exception:
                pass
        self._terminate_process()

    def _ensure_process(self) -> None:
        if self.is_running:
            return
        self._cancelled = False
        self._command_queue = self._context.Queue()
        self._result_queue = self._context.Queue()
        self._process = self._context.Process(
            target=_worker_main,
            args=(self._command_queue, self._result_queue, self.config),
            name="jarvis-chatterbox-process",
            daemon=True,
        )
        self._process.start()

    def _request(self, kind: str, **payload: Any) -> dict[str, Any]:
        self._ensure_process()
        assert self._command_queue is not None
        assert self._result_queue is not None
        request_id = uuid.uuid4().hex
        self._command_queue.put({"kind": kind, "request_id": request_id, **payload})

        while True:
            if self._cancelled:
                raise ChatterboxProcessInterrupted("Geração neural interrompida.")
            process = self._process
            if process is None or not process.is_alive():
                exit_code = None if process is None else process.exitcode
                raise ChatterboxProcessError(
                    f"Processo neural encerrou inesperadamente (exit={exit_code})."
                )
            try:
                result = self._result_queue.get(timeout=0.1)
            except Empty:
                continue
            if result.get("request_id") != request_id:
                continue
            if result.get("kind") == "error":
                details = result.get("traceback") or result.get("message") or "erro desconhecido"
                raise ChatterboxProcessError(
                    "Falha no processo neural: " + str(details).strip()
                )
            return result

    def _terminate_process(self) -> None:
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
        self._process = None
        self._command_queue = None
        self._result_queue = None
