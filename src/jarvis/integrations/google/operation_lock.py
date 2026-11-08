"""Serialização interprocesso das mutações de credencial Google."""

from __future__ import annotations

import ctypes
from pathlib import Path
import os
import sys

from jarvis.integrations.google.errors import GoogleOperationInProgressError


class GoogleOperationLock:
    ERROR_ALREADY_EXISTS = 183

    def __init__(
        self,
        lock_path: Path = Path("data/google/operation.lock"),
        mutex_name: str = r"Local\JARVIS.GoogleOAuth",
    ) -> None:
        self.lock_path = lock_path
        self.mutex_name = mutex_name
        self._handle: int | None = None
        self._file_descriptor: int | None = None

    def acquire(self) -> None:
        if self._handle is not None or self._file_descriptor is not None:
            return
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_wchar_p,
            ]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, True, self.mutex_name)
            if not handle:
                raise GoogleOperationInProgressError(
                    "Não foi possível proteger a operação Google."
                )
            if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                raise GoogleOperationInProgressError(
                    "Outra operação Google já está em andamento."
                )
            self._handle = int(handle)
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._file_descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            raise GoogleOperationInProgressError(
                "Outra operação Google já está em andamento."
            ) from None

    def release(self) -> None:
        if self._handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.ReleaseMutex(self._handle)
            kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._file_descriptor is not None:
            os.close(self._file_descriptor)
            self._file_descriptor = None
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> GoogleOperationLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
