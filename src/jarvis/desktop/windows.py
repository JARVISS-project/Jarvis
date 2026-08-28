"""Backend Win32 sem shell para janelas e lançamento de allowlist."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys

from jarvis.desktop.models import DesktopWindow


class WindowsDesktopBackend:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsDesktopBackend requer Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self._kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
        ]
        self._kernel32.Process32FirstW.restype = wintypes.BOOL
        self._kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
        ]
        self._kernel32.Process32NextW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def list_windows(self) -> list[DesktopWindow]:
        windows: list[DesktopWindow] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def collect(hwnd: int, _lparam: int) -> bool:
            if not self._user32.IsWindowVisible(hwnd):
                return True
            length = int(self._user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if not title:
                return True
            process_id = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(
                hwnd, ctypes.byref(process_id)
            )
            windows.append(
                DesktopWindow(
                    handle=int(hwnd),
                    title=title,
                    process_id=int(process_id.value) or None,
                )
            )
            return True

        callback = callback_type(collect)
        if not self._user32.EnumWindows(callback, 0):
            raise OSError("Não foi possível enumerar janelas do Windows.")
        return windows

    def focus_window(self, handle: int) -> None:
        hwnd = wintypes.HWND(handle)
        if not self._user32.IsWindow(hwnd):
            raise ValueError("A janela deixou de existir.")
        if self._user32.IsIconic(hwnd):
            self._user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        self._user32.BringWindowToTop(hwnd)
        if not self._user32.SetForegroundWindow(hwnd):
            raise RuntimeError(
                "O Windows recusou trazer a janela para primeiro plano."
            )

    def launch_application(self, executable: Path) -> None:
        if not executable.is_absolute() or executable.suffix.casefold() != ".exe":
            raise PermissionError("Executável de desktop inválido.")
        if not executable.is_file():
            raise FileNotFoundError(str(executable))
        subprocess.Popen(  # noqa: S603 - path vem exclusivamente da allowlist.
            [str(executable)],
            shell=False,
            close_fds=True,
        )

    def is_application_running(self, process_names: tuple[str, ...]) -> bool:
        allowed = {name.casefold() for name in process_names}
        if not allowed:
            return False

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        snapshot = self._kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = wintypes.HANDLE(-1).value
        if snapshot == invalid_handle:
            raise OSError(
                ctypes.get_last_error(),
                "Não foi possível enumerar processos do Windows.",
            )

        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            has_entry = bool(
                self._kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            )
            while has_entry:
                if entry.szExeFile.casefold() in allowed:
                    return True
                has_entry = bool(
                    self._kernel32.Process32NextW(snapshot, ctypes.byref(entry))
                )
            return False
        finally:
            self._kernel32.CloseHandle(snapshot)
