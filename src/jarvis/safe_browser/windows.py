from __future__ import annotations

import ctypes
from ctypes import wintypes
from hashlib import sha256
import os

from jarvis.safe_browser.models import ValidatedUri
from jarvis.safe_browser.policy import UrlPolicy


class WindowsDefaultBrowserLauncher:
    ASSOCSTR_EXECUTABLE = 2

    def __init__(self, policy: UrlPolicy) -> None:
        self.policy = policy

    def association_token(self) -> str | None:
        if os.name != "nt":
            return None
        shlwapi = ctypes.WinDLL("shlwapi", use_last_error=True)
        query = shlwapi.AssocQueryStringW
        query.argtypes = [wintypes.DWORD, ctypes.c_int, wintypes.LPCWSTR,
                          wintypes.LPCWSTR, wintypes.LPWSTR,
                          ctypes.POINTER(wintypes.DWORD)]
        query.restype = ctypes.c_long
        size = wintypes.DWORD(0)
        query(0, self.ASSOCSTR_EXECUTABLE, "https", "open", None,
              ctypes.byref(size))
        if size.value <= 1 or size.value > 32768:
            return None
        buffer = ctypes.create_unicode_buffer(size.value)
        result = int(query(0, self.ASSOCSTR_EXECUTABLE, "https", "open",
                           buffer, ctypes.byref(size)))
        executable = buffer.value.strip()
        if result != 0 or not executable:
            return None
        return sha256(executable.casefold().encode("utf-8")).hexdigest()

    def launch(self, request: ValidatedUri) -> bool:
        if not self.policy.is_validated(request):
            raise PermissionError("Launcher recusou URL sem prova de validação.")
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        execute = shell32.ShellExecuteW
        execute.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        execute.restype = ctypes.c_void_p
        result = execute(None, "open", request.canonical_url, None, None, 1)
        return int(result or 0) > 32
