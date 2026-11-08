"""Win32 effects use only validated catalog identities and live instance handles."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.desktop.application_catalog import file_identity
from jarvis.desktop.application_discovery import com_apartment
from jarvis.desktop.windows import WindowsDesktopBackend


@dataclass(frozen=True)
class ApplicationWindow:
    pid: int
    hwnd: int
    birth: int
    thread: int
    class_name: str
    title: str = field(default='', compare=False, repr=False)

    def identity(self) -> str:
        return f'{self.pid}|{self.hwnd}|{self.birth}|{self.thread}|{self.class_name}'


@dataclass(frozen=True)
class MonitorArea:
    id: str
    work: tuple[int, int, int, int]
    primary: bool


def preferred_rectangle(preference: dict, monitors: list[MonitorArea]):
    if not monitors:
        raise RuntimeError('Área útil de monitor não disponível.')
    monitor = next((m for m in monitors if m.id == preference['monitor']), None)
    fallback = monitor is None
    monitor = monitor or next((m for m in monitors if m.primary), monitors[0])
    left, top, right, bottom = monitor.work
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError('Área útil inválida.')
    w, h = max(1, round(width * preference['width'] / 100)), max(1, round(height * preference['height'] / 100))
    x, y = left + round(width * preference['x'] / 100), top + round(height * preference['y'] / 100)
    return (min(max(x, left), right - w), min(max(y, top), bottom - h), w, h), fallback


class WindowsApplicationBackend:
    def __init__(self, desktop=None) -> None:
        self.desktop = desktop or WindowsDesktopBackend()
        self.user, self.kernel = self.desktop._user32, self.desktop._kernel32
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        for name in ('GetApplicationUserModelId', 'GetPackageFullName'):
            function = getattr(self.kernel, name)
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), ctypes.c_wchar_p]
            function.restype = wintypes.LONG
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowThreadProcessId.restype = wintypes.DWORD
        for name in ('IsWindow', 'IsWindowVisible', 'SetForegroundWindow'):
            function = getattr(self.desktop._user32, name)
            function.argtypes = [wintypes.HWND]
            function.restype = wintypes.BOOL
        self.desktop._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.desktop._user32.GetWindowTextLengthW.restype = ctypes.c_int
        for name in ('GetWindowTextW', 'GetClassNameW'):
            function = getattr(self.desktop._user32, name)
            function.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
            function.restype = ctypes.c_int
        self.desktop._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.desktop._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.ShowWindow.restype = wintypes.BOOL
        self.user.IsIconic.argtypes = [wintypes.HWND]
        self.user.IsZoomed.argtypes = [wintypes.HWND]
        self.user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.SetWindowPos.restype = wintypes.BOOL
        self.user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p

    @contextmanager
    def _dpi_coordinates(self):
        previous = self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise RuntimeError('Contexto DPI físico indisponível.')
        try:
            yield
        finally:
            self.user.SetThreadDpiAwarenessContext(previous)

    def _process(self, pid: int):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            size, path = wintypes.DWORD(32768), ctypes.create_unicode_buffer(32768)
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            def package_value(name):
                length = wintypes.UINT()
                function = getattr(self.kernel, name)
                if function(handle, ctypes.byref(length), None) != 122 or length.value > 32768:
                    return ''
                buffer = ctypes.create_unicode_buffer(length.value)
                return buffer.value if function(handle, ctypes.byref(length), buffer) == 0 else ''
            return path.value, (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime, package_value('GetApplicationUserModelId'), package_value('GetPackageFullName')
        finally:
            self.kernel.CloseHandle(handle)

    def windows(self, entry) -> tuple[ApplicationWindow, ...]:
        windows = []
        process_cache = {}
        for window in self.desktop.list_windows():
            if window.process_id is None:
                continue
            pid = window.process_id
            if pid not in process_cache:
                process_cache[pid] = self._process(pid)
            process = process_cache[pid]
            if not process:
                continue
            path, birth, aumid, package = process
            if entry.kind == 'packaged':
                matches = aumid == entry.target and package == entry.locator
            else:
                matches = Path(path).resolve() == Path(entry.target).resolve()
            if not matches:
                continue
            if entry.kind != 'packaged':
                try:
                    # The shortcut fingerprint also binds its source, so compare the executable separately.
                    if file_identity(Path(path)) != file_identity(Path(entry.target)):
                        continue
                except (OSError, ValueError):
                    continue
            thread = self.user.GetWindowThreadProcessId(wintypes.HWND(window.handle), None)
            windows.append(ApplicationWindow(pid, window.handle, birth, int(thread), window.class_name or '', window.title))
        return tuple(sorted(windows, key=lambda w: (w.pid, w.hwnd)))

    def launch(self, entry):
        if entry.kind != 'packaged':
            self.desktop.launch_application(Path(entry.target))
            return
        from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
        from comtypes.client import CreateObject
        class IApplicationActivationManager(IUnknown):
            _iid_ = GUID('{2e941141-7f97-4756-ba1d-9decde894a3d}')
            _methods_ = [COMMETHOD([], HRESULT, 'ActivateApplication',
                (['in'], ctypes.c_wchar_p, 'aumid'), (['in'], ctypes.c_wchar_p, 'arguments'),
                (['in'], wintypes.DWORD, 'options'), (['out'], ctypes.POINTER(wintypes.DWORD), 'pid'))]
        with com_apartment():
            manager = CreateObject('{45BA127D-10A8-46EA-8AB7-56EA9078943C}', interface=IApplicationActivationManager)
            try:
                manager.ActivateApplication(entry.target, None, 0)
            finally:
                del manager

    def perform(self, action: str, window: ApplicationWindow, rectangle=None):
        hwnd = wintypes.HWND(window.hwnd)
        style = self.user.GetWindowLongPtrW(hwnd, -16)
        if action == 'focus':
            self.desktop.focus_window(window.hwnd)
        elif action == 'close':
            self.desktop.request_window_close(window.hwnd)
        elif action in {'minimize', 'maximize', 'restore'}:
            if action in {'minimize', 'maximize'} and not style & (0x20000 if action == 'minimize' else 0x10000):
                raise RuntimeError('Esta janela não suporta a operação solicitada.')
            self.user.ShowWindow(hwnd, {'minimize': 6, 'maximize': 3, 'restore': 9}[action])
        elif action == 'place':
            if not style & 0x40000 or rectangle is None:
                raise RuntimeError('Posição/tamanho não suportados para esta janela.')
            if self.user.IsIconic(hwnd) or self.user.IsZoomed(hwnd):
                raise RuntimeError('Restaure a janela antes de aplicar posição/tamanho.')
            with self._dpi_coordinates():
                if not self.user.SetWindowPos(hwnd, None, *rectangle, 0x14):
                    raise RuntimeError('O Windows recusou posição/tamanho.')
        else:
            raise ValueError('Ação de janela inválida.')

    def verified(self, action: str, window: ApplicationWindow, rectangle=None) -> bool:
        hwnd = wintypes.HWND(window.hwnd)
        if action == 'focus':
            return self.desktop.foreground_window() == window.hwnd
        if action == 'minimize':
            return bool(self.user.IsIconic(hwnd))
        if action == 'maximize':
            return bool(self.user.IsZoomed(hwnd))
        if action == 'restore':
            return not self.user.IsIconic(hwnd) and not self.user.IsZoomed(hwnd)
        if action == 'place':
            with self._dpi_coordinates():
                observed = self.desktop.window_rectangle(window.hwnd)
            return (observed.left, observed.top, observed.right - observed.left, observed.bottom - observed.top) == rectangle
        return False

    def monitors(self) -> list[MonitorArea]:
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT), ('work', wintypes.RECT), ('flags', wintypes.DWORD), ('device', wintypes.WCHAR * 32)]
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        self.user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        self.user.GetMonitorInfoW.restype = wintypes.BOOL
        self.user.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, callback_type, wintypes.LPARAM]
        self.user.EnumDisplayMonitors.restype = wintypes.BOOL
        monitors = []
        def collect(handle, dc, rectangle, data):
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            if self.user.GetMonitorInfoW(handle, ctypes.byref(info)):
                monitors.append(MonitorArea(info.device, (info.work.left, info.work.top, info.work.right, info.work.bottom), bool(info.flags & 1)))
            return True
        with self._dpi_coordinates():
            if not self.user.EnumDisplayMonitors(None, None, callback_type(collect), 0):
                raise RuntimeError('Monitores não disponíveis.')
        return monitors
