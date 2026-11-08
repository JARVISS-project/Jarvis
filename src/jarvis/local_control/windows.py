from __future__ import annotations

import ctypes
import os
from pathlib import Path
import uuid

from ctypes import wintypes

from jarvis.desktop.windows import WindowsDesktopBackend
from jarvis.local_control.models import ApplicationInstance, ApplicationState


class WindowsLocalControlBackend:
    """Win32 adapter with a fixed internal allowlist and no caller-supplied paths."""

    def __init__(self, desktop: WindowsDesktopBackend | None = None) -> None:
        self._desktop = desktop or WindowsDesktopBackend()
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        roaming = Path(os.environ.get("APPDATA", ""))
        candidates = {
            "spotify": (roaming / "Spotify" / "Spotify.exe", ("spotify.exe",)),
            "obsidian": (local / "Programs" / "Obsidian" / "Obsidian.exe", ("obsidian.exe",)),
        }
        self._applications = {
            app_id: (path.resolve(), names)
            for app_id, (path, names) in candidates.items()
            if path.is_absolute() and path.is_file()
        }

    def available_applications(self) -> frozenset[str]:
        return frozenset(self._applications)

    def inspect_application(self, application_id: str) -> ApplicationState:
        _path, process_names = self._spec(application_id)
        allowed = {name.casefold() for name in process_names}
        instances = tuple(sorted((
            ApplicationInstance(
                application_id, window.process_id, window.handle,
                window.class_name or "",
            )
            for window in self._desktop.list_windows()
            if window.process_id is not None
            and self._process_name(window.process_id).casefold() in allowed
        ), key=lambda item: (item.process_id, item.window_handle)))
        return ApplicationState(application_id, instances)

    def open_application(self, application_id: str) -> None:
        path, _names = self._spec(application_id)
        self._desktop.launch_application(path)

    def focus_application(self, application_id: str, window_handle: int) -> None:
        self._spec(application_id)
        self._desktop.focus_window(window_handle)

    def request_close(self, application_id: str, window_handle: int) -> None:
        self._spec(application_id)
        self._desktop.request_window_close(window_handle)

    def volume_available(self) -> bool:
        if os.name != "nt":
            return False
        try:
            self.get_volume()
        except (OSError, RuntimeError):
            return False
        return True

    def get_volume(self) -> tuple[int, bool]:
        with _DefaultEndpointVolume() as endpoint:
            return endpoint.get()

    def set_volume(self, percent: int) -> None:
        if (
            not isinstance(percent, int)
            or isinstance(percent, bool)
            or not 0 <= percent <= 100
        ):
            raise ValueError("Volume deve ser um inteiro entre 0 e 100.")
        with _DefaultEndpointVolume() as endpoint:
            endpoint.set_volume(percent)

    def set_muted(self, muted: bool) -> None:
        if not isinstance(muted, bool):
            raise ValueError("Estado de mudo inválido.")
        with _DefaultEndpointVolume() as endpoint:
            endpoint.set_muted(muted)

    def _spec(self, application_id: str) -> tuple[Path, tuple[str, ...]]:
        try:
            return self._applications[application_id]
        except KeyError as error:
            raise PermissionError("Aplicativo indisponível na allowlist efetiva.") from error

    def _process_name(self, process_id: int) -> str:
        names = self._desktop._application_process_ids(  # noqa: SLF001
            tuple(name for _path, candidates in self._applications.values() for name in candidates)
        )
        if process_id not in names:
            return ""
        # The existing backend exposes only membership, not the image name. Resolve by
        # testing each fixed allowlisted name; no caller data reaches this boundary.
        for _path, candidates in self._applications.values():
            for name in candidates:
                if process_id in self._desktop._application_process_ids((name,)):  # noqa: SLF001
                    return name
        return ""


def build_windows_local_control_backend() -> WindowsLocalControlBackend:
    return WindowsLocalControlBackend()


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "_Guid":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _DefaultEndpointVolume:
    """Minimal COM binding for the documented default render endpoint."""

    CLSID_ENUMERATOR = _Guid.parse("bcde0395-e52f-467c-8e3d-c4579291692e")
    IID_ENUMERATOR = _Guid.parse("a95664d2-9614-4f35-a746-de8db63617e6")
    IID_ENDPOINT_VOLUME = _Guid.parse("5cdf2c82-841e-4546-9722-0cf74078229a")

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Core Audio requer Windows.")
        self._ole32 = ctypes.OleDLL("ole32")
        self._initialized = False
        self._enumerator = ctypes.c_void_p()
        self._device = ctypes.c_void_p()
        self._endpoint = ctypes.c_void_p()

    def __enter__(self) -> "_DefaultEndpointVolume":
        result = int(self._ole32.CoInitializeEx(None, 0x2))
        if result not in (0, 1, -2147417850):
            self._check(result, "CoInitializeEx")
        self._initialized = result in (0, 1)
        try:
            result = int(self._ole32.CoCreateInstance(
                ctypes.byref(self.CLSID_ENUMERATOR), None, 1,
                ctypes.byref(self.IID_ENUMERATOR), ctypes.byref(self._enumerator),
            ))
            self._check(result, "CoCreateInstance")
            if not self._enumerator.value:
                raise RuntimeError("Enumerador Core Audio indisponível.")
            self._invoke(self._enumerator, 4, ctypes.c_long,
                         (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                         0, 0, ctypes.byref(self._device))
            self._invoke(self._device, 3, ctypes.c_long,
                         (ctypes.POINTER(_Guid), ctypes.c_ulong, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_void_p)),
                         ctypes.byref(self.IID_ENDPOINT_VOLUME), 1, None,
                         ctypes.byref(self._endpoint))
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *_args: object) -> None:
        for pointer in (self._endpoint, self._device, self._enumerator):
            if pointer.value:
                self._invoke(pointer, 2, ctypes.c_ulong, ())
                pointer.value = None
        if self._initialized:
            self._ole32.CoUninitialize()
            self._initialized = False

    def get(self) -> tuple[int, bool]:
        scalar = ctypes.c_float()
        muted = wintypes.BOOL()
        self._invoke(self._endpoint, 9, ctypes.c_long,
                     (ctypes.POINTER(ctypes.c_float),), ctypes.byref(scalar))
        self._invoke(self._endpoint, 15, ctypes.c_long,
                     (ctypes.POINTER(wintypes.BOOL),), ctypes.byref(muted))
        return round(float(scalar.value) * 100), bool(muted.value)

    def set_volume(self, percent: int) -> None:
        self._invoke(self._endpoint, 7, ctypes.c_long,
                     (ctypes.c_float, ctypes.c_void_p),
                     ctypes.c_float(percent / 100), None)

    def set_muted(self, muted: bool) -> None:
        self._invoke(self._endpoint, 14, ctypes.c_long,
                     (wintypes.BOOL, ctypes.c_void_p), wintypes.BOOL(muted), None)

    @staticmethod
    def _invoke(pointer: ctypes.c_void_p, index: int, restype: object,
                argument_types: tuple[object, ...], *arguments: object) -> int:
        table = ctypes.cast(
            pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        function = ctypes.WINFUNCTYPE(
            restype, ctypes.c_void_p, *argument_types
        )(table[index])
        result = int(function(pointer, *arguments))
        if restype is ctypes.c_long:
            _DefaultEndpointVolume._check(result, f"Core Audio vtable[{index}]")
        return result

    @staticmethod
    def _check(result: int, operation: str) -> None:
        if result < 0:
            raise OSError(result, f"{operation} falhou")
