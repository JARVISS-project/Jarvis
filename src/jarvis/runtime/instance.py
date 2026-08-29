"""Single-instance local com mutex Win32 e metadata de diagnóstico."""

from __future__ import annotations

from collections.abc import Callable
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import uuid


class InstanceAlreadyRunningError(RuntimeError):
    pass


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class SingleInstanceGuard:
    """Uses an OS-owned mutex on Windows; the JSON file is not authority."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(
        self,
        metadata_path: Path,
        *,
        mutex_name: str = r"Local\JARVIS.Runtime",
        pid: int | None = None,
        is_process_running: Callable[[int], bool] = process_is_running,
        platform: str | None = None,
    ) -> None:
        self.metadata_path = metadata_path
        self.mutex_name = mutex_name
        self.pid = pid or os.getpid()
        self.instance_id = uuid.uuid4().hex
        self._is_process_running = is_process_running
        self._platform = platform or sys.platform
        self._mutex: int | None = None
        self._file_descriptor: int | None = None
        self._acquired = False

    def acquire(self) -> None:
        if self._acquired:
            return
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        if self._platform == "win32":
            self._acquire_windows_mutex()
        else:
            self._acquire_file_lock()
        try:
            self._write_metadata()
        except Exception:
            self.release()
            raise
        self._acquired = True

    def release(self) -> None:
        if self._metadata_belongs_to_self():
            try:
                self.metadata_path.unlink()
            except FileNotFoundError:
                pass
        if self._mutex is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle(self._mutex)
            self._mutex = None
        if self._file_descriptor is not None:
            os.close(self._file_descriptor)
            self._file_descriptor = None
            try:
                self.metadata_path.with_suffix(".lock").unlink()
            except FileNotFoundError:
                pass
        self._acquired = False

    def _acquire_windows_mutex(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self.mutex_name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW falhou")
        if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise InstanceAlreadyRunningError("Outra instância do JARVIS já está ativa.")
        self._mutex = int(handle)

    def _acquire_file_lock(self) -> None:
        lock_path = self.metadata_path.with_suffix(".lock")
        for _attempt in range(2):
            try:
                self._file_descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                return
            except FileExistsError:
                pid = self._read_pid()
                if pid is not None and self._is_process_running(pid):
                    raise InstanceAlreadyRunningError(
                        "Outra instância do JARVIS já está ativa."
                    )
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
        raise InstanceAlreadyRunningError("Não foi possível adquirir o lock local.")

    def _read_pid(self) -> int | None:
        try:
            value = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            pid = value.get("pid")
            return pid if isinstance(pid, int) else None
        except (OSError, ValueError, TypeError):
            return None

    def _write_metadata(self) -> None:
        payload = {
            "pid": self.pid,
            "instance_id": self.instance_id,
            "acquired_at": time.time(),
        }
        temporary = self.metadata_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(self.metadata_path)

    def _metadata_belongs_to_self(self) -> bool:
        try:
            value = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        return value.get("instance_id") == self.instance_id

    def __enter__(self) -> SingleInstanceGuard:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
