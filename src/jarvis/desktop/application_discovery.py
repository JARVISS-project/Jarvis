"""Bounded Windows registry/known-folder discovery. No shell command parsing."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import _ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import re
import sys
from threading import RLock

from jarvis.desktop.application_catalog import (
    DiscoveredApplication, executable_kind, file_identity, opaque_id,
)

COM_ERROR = getattr(_ctypes, 'COMError', OSError)
_COM_IMPORT_LOCK = RLock()


@contextmanager
def com_apartment():
    ole = ctypes.OleDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    result = ole.CoInitializeEx(None, 2)
    if result < 0:
        raise RuntimeError('Apartment COM indisponível para descoberta/ativação.')
    try:
        yield
    finally:
        ole.CoUninitialize()


def shell_link(path: Path) -> tuple[str, str]:
    """Read IShellLinkW/IPersistFile; never resolve, launch or save the shortcut."""
    from comtypes import GUID
    ole = ctypes.OleDLL('ole32')
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    pointer = ctypes.c_void_p()
    result = ole.CoCreateInstance(ctypes.byref(GUID('{00021401-0000-0000-C000-000000000046}')), None, 1,
        ctypes.byref(GUID('{000214F9-0000-0000-C000-000000000046}')), ctypes.byref(pointer))
    if result < 0:
        raise OSError('IShellLink indisponível.')
    def method(obj, index, result_type, *arguments):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *arguments)(table[index])
    persist = ctypes.c_void_p()
    try:
        hr = method(pointer, 0, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            pointer, ctypes.byref(GUID('{0000010b-0000-0000-C000-000000000046}')), ctypes.byref(persist))
        if hr < 0:
            raise OSError('IPersistFile indisponível.')
        if method(persist, 5, ctypes.c_long, ctypes.c_wchar_p, wintypes.DWORD)(persist, str(path), 0) < 0:
            raise OSError('Atalho não carregado.')
        target, arguments = ctypes.create_unicode_buffer(32768), ctypes.create_unicode_buffer(32768)
        if method(pointer, 3, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)(pointer, target, len(target), None, 4) < 0:
            raise OSError('Destino do atalho indisponível.')
        if method(pointer, 10, ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int)(pointer, arguments, len(arguments)) < 0:
            raise OSError('Argumentos do atalho indisponíveis.')
        return target.value, arguments.value
    finally:
        if persist:
            method(persist, 2, wintypes.ULONG)(persist)
        method(pointer, 2, wintypes.ULONG)(pointer)


class WindowsApplicationDiscovery:
    def __init__(self) -> None:
        if os.name != 'nt':
            raise RuntimeError('Catálogo Windows indisponível.')
        # comtypes initializes STA on first import. Balance that implicit call
        # as well as our explicit scope; do not leave the main thread in STA
        # and break the existing WinRT diagnostic/worker ownership.
        with _COM_IMPORT_LOCK, com_apartment():
            first_import = 'comtypes' not in sys.modules
            import comtypes
            if first_import:
                comtypes.CoUninitialize()

    @staticmethod
    def _win32(name: str, source: str, locator: str, target: str, arguments: str = '', legacy=None) -> DiscoveredApplication:
        kind, fingerprint, reason = 'unsupported', '', 'Destino/entrada indisponível.'
        try:
            # App Paths stores one path, not a command line. Quotes/parameters fail closed.
            if not isinstance(target, str) or any(c in target for c in ('"', '\0', '\n', '\r')) or target.startswith(('\\\\', '//')):
                raise ValueError
            path = Path(target)
            kind = executable_kind(path)
            if arguments:
                kind, reason = 'shortcut_with_arguments', 'Atalho exige argumentos; não suportado nesta fase.'
            elif kind == 'application':
                fingerprint = file_identity(path)
                if source == 'start_menu':
                    fingerprint = hashlib.sha256((fingerprint + file_identity(Path(locator))).encode()).hexdigest()
                reason = ''
            else:
                reason = 'Instalador/CLI/script/destino não GUI: indisponível nesta fase.'
        except (ValueError, OSError):
            kind, reason = 'unavailable', 'Destino ausente ou identidade não validada.'
        return DiscoveredApplication(opaque_id(source, locator), name[:200], source, locator, target,
            fingerprint, kind, bool(fingerprint), reason, legacy)

    def discover(self):
        import winreg
        entries, diagnostics = [], []
        key_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths'
        for hive_name, hive in (('HKCU', winreg.HKEY_CURRENT_USER), ('HKLM', winreg.HKEY_LOCAL_MACHINE)):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                        for index in range(min(winreg.QueryInfoKey(key)[0], 2000)):
                            subkey = winreg.EnumKey(key, index)
                            locator = f'{hive_name}|{view}|{subkey}'
                            try:
                                with winreg.OpenKey(key, subkey) as child:
                                    target, value_type = winreg.QueryValueEx(child, '')
                                if value_type not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ} or not isinstance(target, str):
                                    raise ValueError('Registro não contém um caminho tipado.')
                                if value_type == winreg.REG_EXPAND_SZ:
                                    target = os.path.expandvars(target)
                            except (OSError, ValueError):
                                target = ''
                                diagnostics.append('Algumas entradas App Paths não puderam ser consultadas.')
                            entries.append(self._win32(Path(subkey).stem, 'app_paths', locator, target))
                except FileNotFoundError:
                    pass
                except (OSError, TypeError):
                    diagnostics.append('Algumas entradas App Paths não puderam ser consultadas.')
        try:
            with com_apartment():
                for root in self._program_folders():
                    for directory, folders, files in os.walk(root, followlinks=False):
                        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink() and not getattr(Path(directory) / name, 'is_junction', lambda: False)()]
                        for name in files:
                            if len(entries) >= 5000:
                                raise RuntimeError('Limite de descoberta atingido.')
                            path = Path(directory) / name
                            if path.suffix.casefold() != '.lnk':
                                continue
                            try:
                                target, arguments = shell_link(path)
                                entries.append(self._win32(path.stem, 'start_menu', str(path), target, arguments))
                            except (OSError, ValueError):
                                entries.append(self._win32(path.stem, 'start_menu', str(path), ''))
                entries.extend(self._packaged())
        except (ImportError, OSError, RuntimeError, COM_ERROR):
            diagnostics.append('Descoberta Shell/empacotados indisponível ou parcial; não há autorização implícita.')
        # Preserve the existing fixed Spotify/Obsidian routes, not arbitrary similarly named apps.
        for entry in self.legacy_entries():
            entries = [e for e in entries if e.target.casefold() != entry.target.casefold()]
            entries.append(entry)
        return entries, sorted(set(diagnostics))

    def legacy_entries(self):
        entries = []
        for legacy, name, path in (
            ('spotify', 'Spotify', Path(os.environ.get('APPDATA', '')) / 'Spotify/Spotify.exe'),
            ('obsidian', 'Obsidian', Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Obsidian/Obsidian.exe'),
        ):
            if path.is_absolute() and path.is_file():
                entry = self._win32(name, 'legacy_allowlist', legacy, str(path), legacy=legacy)
                entries.append(entry)
        return entries

    @staticmethod
    def _program_folders() -> tuple[Path, ...]:
        from comtypes import GUID
        shell, ole = ctypes.WinDLL('shell32'), ctypes.OleDLL('ole32')
        shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        roots = []
        for guid in ('{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}', '{0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8}'):
            pointer = ctypes.c_void_p()
            if shell.SHGetKnownFolderPath(ctypes.byref(GUID(guid)), 0, None, ctypes.byref(pointer)) >= 0:
                try:
                    roots.append(Path(ctypes.wstring_at(pointer)))
                finally:
                    ole.CoTaskMemFree(pointer)
        return tuple(roots)

    @staticmethod
    def _packaged():
        from comtypes.client import CreateObject
        shell = CreateObject('Shell.Application', dynamic=True)
        folder = shell.NameSpace('shell:AppsFolder')
        items = folder.Items()
        entries = []
        for index in range(min(items.Count, 2000)):
            item = items.Item(index)
            aumid = str(item.ExtendedProperty('System.AppUserModel.ID') or '')
            if not re.fullmatch(r'[A-Za-z0-9._-]+![A-Za-z0-9._-]+', aumid):
                continue
            package = str(item.ExtendedProperty('System.AppUserModel.PackageFullName') or '')
            path = str(item.ExtendedProperty('System.AppUserModel.PackageInstallPath') or '')
            valid = bool(package and path and Path(path).is_absolute())
            fingerprint = hashlib.sha256(f'{aumid}|{package}|{path}'.encode()).hexdigest() if valid else ''
            entries.append(DiscoveredApplication(opaque_id('apps_folder', aumid), str(item.Name)[:200],
                'apps_folder', package, aumid, fingerprint, 'packaged', valid,
                '' if valid else 'Identidade completa do pacote não comprovada.'))
        # CPython releases these wrappers in their owning apartment before the
        # caller leaves its COM scope. No COM pointer crosses this boundary.
        if items.Count:
            del item
        del items, folder, shell
        return entries

    def revalidate(self, entry: DiscoveredApplication) -> str:
        if entry.source == 'apps_folder':
            with com_apartment():
                current = next((e for e in self._packaged() if e.id == entry.id), None)
            return current.fingerprint if current else ''
        if entry.source == 'start_menu':
            with com_apartment():
                target, arguments = shell_link(Path(entry.locator))
            return self._win32(entry.name, entry.source, entry.locator, target, arguments).fingerprint
        if entry.source == 'app_paths':
            import winreg
            hive, view, name = entry.locator.split('|', 2)
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER if hive == 'HKCU' else winreg.HKEY_LOCAL_MACHINE,
                    r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + name, 0, winreg.KEY_READ | int(view)) as key:
                    target, kind = winreg.QueryValueEx(key, '')
                if kind == winreg.REG_EXPAND_SZ:
                    target = os.path.expandvars(target)
                return self._win32(entry.name, entry.source, entry.locator, target).fingerprint
            except OSError:
                return ''
        return self._win32(entry.name, entry.source, entry.locator, entry.target).fingerprint
