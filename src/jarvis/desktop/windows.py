"""Backend Win32 sem shell para janelas e lançamento de allowlist."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys

from jarvis.desktop.models import DesktopWindow, WindowRectangle


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("virtual_key", wintypes.WORD),
        ("scan_code", wintypes.WORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouse_data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("message", wintypes.DWORD),
        ("parameter_low", wintypes.WORD),
        ("parameter_high", wintypes.WORD),
    ]


class _InputValue(ctypes.Union):
    _fields_ = [
        ("mouse", _MouseInput),
        ("keyboard", _KeyboardInput),
        ("hardware", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _InputValue)]


class WindowsDesktopBackend:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsDesktopBackend requer Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self._user32.GetWindowRect.restype = wintypes.BOOL
        self._user32.SetCursorPos.argtypes = [wintypes.INT, wintypes.INT]
        self._user32.SetCursorPos.restype = wintypes.BOOL
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._user32.GetCursorPos.restype = wintypes.BOOL
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(_Input),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.PostMessageW.restype = wintypes.BOOL
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
            class_buffer = ctypes.create_unicode_buffer(256)
            self._user32.GetClassNameW(hwnd, class_buffer, 256)
            windows.append(
                DesktopWindow(
                    handle=int(hwnd),
                    title=title,
                    process_id=int(process_id.value) or None,
                    class_name=class_buffer.value or None,
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
        if self.foreground_window() == handle:
            return
        accepted = bool(self._user32.SetForegroundWindow(hwnd))
        if not accepted and self.foreground_window() != handle:
            raise RuntimeError(
                "O Windows recusou trazer a janela para primeiro plano. "
                "Traga-a para frente manualmente e tente novamente."
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

    def window_rectangle(self, handle: int) -> WindowRectangle:
        hwnd = wintypes.HWND(handle)
        if not self._user32.IsWindow(hwnd):
            raise ValueError("A janela deixou de existir.")
        if self._user32.IsIconic(hwnd):
            raise RuntimeError(
                "A janela permaneceu minimizada após a tentativa de restauração."
            )
        rectangle = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
            raise OSError(
                ctypes.get_last_error(),
                "Não foi possível obter os limites da janela.",
            )
        return WindowRectangle(
            int(rectangle.left),
            int(rectangle.top),
            int(rectangle.right),
            int(rectangle.bottom),
        )

    def move_pointer(self, x: int, y: int) -> None:
        if not self._user32.SetCursorPos(x, y):
            raise OSError(
                ctypes.get_last_error(),
                "Não foi possível mover o ponteiro.",
            )

    def pointer_position(self) -> tuple[int, int]:
        point = wintypes.POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            raise OSError(
                ctypes.get_last_error(),
                "Não foi possível verificar o ponteiro.",
            )
        return int(point.x), int(point.y)

    def foreground_window(self) -> int | None:
        handle = self._user32.GetForegroundWindow()
        return int(handle) if handle else None

    def set_escape_pressed(self, pressed: bool) -> None:
        key_up = 0x0002
        event = _Input(
            type=1,
            keyboard=_KeyboardInput(
                virtual_key=0x1B,
                flags=0 if pressed else key_up,
            ),
        )
        sent = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
        if sent != 1:
            raise OSError(
                ctypes.get_last_error(),
                "O Windows recusou o evento de teclado.",
            )

    def request_window_close(self, handle: int) -> None:
        hwnd = wintypes.HWND(handle)
        if not self._user32.IsWindow(hwnd):
            raise ValueError("A janela deixou de existir.")
        if not self._user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
            raise OSError(
                ctypes.get_last_error(),
                "O Windows recusou a solicitação de fechamento.",
            )

    def window_exists(self, handle: int) -> bool:
        return bool(self._user32.IsWindow(wintypes.HWND(handle)))

    def is_application_running(self, process_names: tuple[str, ...]) -> bool:
        return bool(self._application_process_ids(process_names))

    def has_application_window(
        self,
        process_names: tuple[str, ...],
        window_classes: tuple[str, ...],
    ) -> bool:
        process_ids = self._application_process_ids(process_names)
        if not process_ids:
            return False
        allowed_classes = {name.casefold() for name in window_classes}
        return any(
            window.process_id in process_ids
            and (
                not allowed_classes
                or (window.class_name or "").casefold() in allowed_classes
            )
            for window in self.list_windows()
        )

    def _application_process_ids(
        self,
        process_names: tuple[str, ...],
    ) -> set[int]:
        allowed = {name.casefold() for name in process_names}
        if not allowed:
            return set()

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
        process_ids: set[int] = set()
        try:
            has_entry = bool(
                self._kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            )
            while has_entry:
                if entry.szExeFile.casefold() in allowed:
                    process_ids.add(int(entry.th32ProcessID))
                has_entry = bool(
                    self._kernel32.Process32NextW(snapshot, ctypes.byref(entry))
                )
            return process_ids
        finally:
            self._kernel32.CloseHandle(snapshot)
