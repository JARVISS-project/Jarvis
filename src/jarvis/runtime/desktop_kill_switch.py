"""Hotkey global de emergência para bloquear controle de desktop no Windows."""

import ctypes
from threading import Event, Thread
import sys

from jarvis.desktop.control import DesktopKillSwitch


class WindowsDesktopKillSwitchListener:
    """Escuta Ctrl+Alt+K sem passar pelo LLM ou pela UI.

    Se o hotkey não puder ser registrado, o kill switch é acionado para manter
    o controle de desktop fail-closed.
    """

    HOTKEY_ID = 0x4A56
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000
    VK_K = 0x4B

    def __init__(self, kill_switch: DesktopKillSwitch) -> None:
        self.kill_switch = kill_switch
        self._thread: Thread | None = None
        self._started = Event()
        self._registered = Event()
        self._thread_id: int | None = None
        self._stop_requested = Event()

    @property
    def is_registered(self) -> bool:
        return self._registered.is_set()

    def start(self, timeout_seconds: float = 2.0) -> bool:
        if sys.platform != "win32":
            self.kill_switch.trip("hotkey de emergência requer Windows")
            return False
        if self._thread is not None and self._thread.is_alive():
            return self.is_registered
        self._stop_requested.clear()
        self._started.clear()
        self._registered.clear()
        self._thread = Thread(
            target=self._run,
            name="jarvis-desktop-kill-switch",
            daemon=True,
        )
        self._thread.start()
        self._started.wait(timeout_seconds)
        if not self.is_registered:
            self.kill_switch.trip("hotkey Ctrl+Alt+K indisponível")
            return False
        return True

    def stop(self) -> None:
        self._stop_requested.set()
        if sys.platform == "win32" and self._thread_id is not None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = None
        self._registered.clear()

    def _run(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        modifiers = self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT
        registered = bool(
            user32.RegisterHotKey(None, self.HOTKEY_ID, modifiers, self.VK_K)
        )
        if registered:
            self._registered.set()
        self._started.set()
        if not registered:
            return

        message = wintypes.MSG()
        try:
            while not self._stop_requested.is_set():
                result = int(user32.GetMessageW(ctypes.byref(message), None, 0, 0))
                if result <= 0:
                    break
                if (
                    message.message == self.WM_HOTKEY
                    and int(message.wParam) == self.HOTKEY_ID
                ):
                    self.kill_switch.trip("Ctrl+Alt+K acionado pelo usuário")
                    print(
                        "\n[KILL SWITCH] Controle de desktop bloqueado. "
                        "Reinicie o Jarvis para rearmar."
                    )
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)
            self._registered.clear()
            if not self._stop_requested.is_set() and not self.kill_switch.is_tripped:
                self.kill_switch.trip(
                    "listener do kill switch encerrou inesperadamente"
                )
