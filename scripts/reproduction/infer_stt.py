"""Controlled offline STT subprocess probe; no microphone, Tools or TTS."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import ctypes
from ctypes import wintypes
import json
import importlib.util
import os
from pathlib import Path
import sys
import time
import wave

_spec = importlib.util.spec_from_file_location(
    "reproduction_verify_models", Path(__file__).with_name("verify_models.py")
)
assert _spec is not None and _spec.loader is not None
_verification = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_verification)
sha256 = _verification.sha256
verify_files = _verification.verify_files


def process_metrics(pid: int) -> dict:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x0410, False, pid)
    if not handle:
        return {"error": "OpenProcess"}
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in
                      ("peak", "working", "quota_peak_paged", "quota_paged",
                       "quota_peak_nonpaged", "quota_nonpaged", "pagefile", "peak_pagefile")]]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                         wintypes.LPWSTR, wintypes.DWORD]
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        modules = (wintypes.HMODULE * 2048)()
        needed = wintypes.DWORD()
        paths = []
        if psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules),
                                     ctypes.byref(needed), 3):
            for module in modules[:min(needed.value // ctypes.sizeof(wintypes.HMODULE), 2048)]:
                buffer = ctypes.create_unicode_buffer(32768)
                if psapi.GetModuleFileNameExW(handle, module, buffer, len(buffer)):
                    path = Path(buffer.value)
                    if path.name.lower().startswith(("cublas", "cudnn", "cudart")):
                        own = path.is_relative_to(Path(sys.prefix))
                        paths.append({"file": path.name, "isolated_venv": own,
                                      "origin": path.relative_to(Path(sys.prefix)).as_posix()
                                      if own else "external/" + path.name,
                                      "sha256": sha256(path)})
        return {"working_set_bytes": counters.working if ok else None,
                "peak_working_set_bytes": counters.peak if ok else None,
                "cuda_dlls": paths}
    finally:
        kernel.CloseHandle(handle)


def resource_status(resources: dict) -> str:
    if (resources.get("error") or resources.get("working_set_bytes") is None
            or resources.get("peak_working_set_bytes") is None
            or not resources.get("cuda_dlls")):
        return "incomplete_resource_evidence"
    if any(not dll["isolated_venv"] for dll in resources["cuda_dlls"]):
        return "external_dll_dependency"
    return "passed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    files = verify_files(args.model, manifest["stt"]["files"])
    if any(row["status"] != "match" for row in files.values()):
        print(json.dumps({"status": "blocked_model_identity", "files": files}))
        return 1
    if sha256(args.wav) != manifest["public_wav"]["sha256"]:
        print(json.dumps({"status": "blocked_wav_identity"}))
        return 1
    from jarvis.speech.gpu_budget import GpuBudgetManager, query_gpu_memory
    from jarvis.speech.stt_process import SttModelConfig, SttProcessClient
    budget = GpuBudgetManager().assess(5781)
    if budget.status != "ready":
        print(json.dumps({"status": "blocked_vram", "budget": asdict(budget)}))
        return 1
    with wave.open(str(args.wav), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        import numpy as np
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").tolist()
    # Explicit own wheel DLL directory, not inherited PATH or habitual/user-site.
    directory = Path(sys.prefix) / "Lib/site-packages/torch/lib"
    directory_handle = os.add_dll_directory(str(directory))
    # Spawn inherits this explicit minimal DLL path only; no habitual PATH.
    os.environ["PATH"] = str(directory) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    client = SttProcessClient(SttModelConfig(str(args.model.resolve())))
    report = {"budget_before": asdict(budget), "host_models_reused": True,
              "path_policy": "own torch/lib plus Windows System32"}
    try:
        start = time.perf_counter()
        client.warm_up()
        report["load_seconds"] = time.perf_counter() - start
        report["vram_loaded"] = asdict(query_gpu_memory())
        start = time.perf_counter()
        result = client.transcribe(pcm, 16000)
        report["inference_seconds"] = time.perf_counter() - start
        report["result"] = asdict(result)
        report["resources"] = process_metrics(client.worker_pid)
        report["status"] = resource_status(report["resources"])
    except Exception as error:
        report["status"] = "failed"
        report["error"] = type(error).__name__
        report["reason"] = getattr(error, "reason_code", None)
    finally:
        start = time.perf_counter()
        client.close()
        report["close_seconds"] = time.perf_counter() - start
        report["worker_residual"] = client.worker_alive
        report["vram_final"] = asdict(query_gpu_memory())
        directory_handle.close()
    print(json.dumps(report, ensure_ascii=True))
    return int(report["status"] != "passed" or report["worker_residual"])


if __name__ == "__main__":
    raise SystemExit(main())
