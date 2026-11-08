"""Local Windows no-replace rename; handles pin files and directory ancestors."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import secrets
import stat


def safe_path(path: Path) -> None:
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')) or len(str(path)) >= 240:
        raise PermissionError('Caminho especial, remoto ou longo não suportado.')
    for part in (path, *path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise PermissionError('Link/junction/reparse point bloqueado.')


def fingerprint(stream) -> dict:
    before = os.fstat(stream.fileno())
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise PermissionError('Somente arquivo regular sem hardlinks.')
    if before.st_size > 128 * 1024 * 1024:
        raise ValueError('Arquivo excede limite de integridade de 128 MiB.')
    stream.seek(0)
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after):
        raise PermissionError('Arquivo mudou durante análise.')
    return {'device': before.st_dev, 'inode': before.st_ino, 'size': before.st_size,
        'mtime': before.st_mtime_ns, 'sha256': digest}


class WindowsMoves:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('Organização segura disponível somente no Windows.')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        k = self.kernel
        k.CreateFileW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.GetFileInformationByHandleEx.restype = wintypes.BOOL
        k.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.SetFileInformationByHandle.restype = wintypes.BOOL
        k.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        k.GetDriveTypeW.restype = wintypes.UINT

    def _open(self, path, access, share):
        safe_path(path)
        handle = self.kernel.CreateFileW(str(path), access, share, None, 3, 0x00200000 | 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), 'Arquivo/diretório bloqueado, em uso ou inacessível.')
        return handle

    @contextmanager
    def directories(self, *paths, writable=None):
        with ExitStack() as stack:
            pinned = set()
            for path in paths:
                for part in reversed((path, *path.parents)):
                    if part in pinned:
                        continue
                    # Attribute-only handles do not enforce share-delete locking.
                    # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES pins the namespace.
                    handle = self._open(part, 0x81, 3 if part == writable else 1)
                    stack.callback(self.kernel.CloseHandle, handle)
                    safe_path(part)
                    flags = wintypes.DWORD()
                    if not self.kernel.GetFileInformationByHandleEx(handle, 23, ctypes.byref(flags), ctypes.sizeof(flags)) or flags.value:
                        raise PermissionError('Diretório case-sensitive ou garantia de caixa indisponível.')
                    if not part.is_dir():
                        raise PermissionError('Ancestral não é diretório.')
                    pinned.add(part)
            yield

    @contextmanager
    def destination_guard(self, directory):
        # Rename needs FILE_WRITE_DATA on its target directory. First prevent
        # reparse mutation with a read-only share lock, then keep a private,
        # exclusive child open: a nonempty directory cannot become a reparse
        # point. DELETE_ON_CLOSE removes only this newly created marker.
        readonly = self._open(directory, 0x81, 1)
        marker = None
        try:
            safe_path(directory)
            path = directory / ('.jarvis-' + secrets.token_hex(16) + '.tmp')
            safe_path(path)
            marker = self.kernel.CreateFileW(str(path), 0xC0010000, 0, None, 1,
                0x04000000 | 0x00200000, None)
            if marker == ctypes.c_void_p(-1).value:
                marker = None
                raise OSError(ctypes.get_last_error(), 'Proteção do destino indisponível.')
        finally:
            self.kernel.CloseHandle(readonly)
        try:
            yield
        finally:
            if marker is not None:
                self.kernel.CloseHandle(marker)

    @contextmanager
    def file(self, path, *, moving=False):
        import msvcrt
        handle = self._open(path, 0x80000000 | (0x10000 if moving else 0), 1)  # read; deny writes/deletes
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise
        with os.fdopen(descriptor, 'rb') as stream:
            safe_path(path)
            yield stream

    def root_identity(self, root):
        safe_path(root)
        if self.kernel.GetDriveTypeW(root.anchor) != 3:
            raise PermissionError('Somente volume local fixo; sem rede/cópia entre volumes.')
        with self.directories(root):
            info = root.stat()
            return [info.st_dev, info.st_ino]

    def inspect(self, path):
        with self.directories(path.parent), self.file(path) as stream:
            return fingerprint(stream)

    def move(self, root, source, destination, expected, check):
        import msvcrt
        safe_path(source)
        safe_path(destination)
        if not source.is_relative_to(root) or not destination.is_relative_to(root):
            raise PermissionError('Movimento fora da raiz selecionada.')
        with self.directories(root, source.parent, writable=destination.parent), ExitStack() as stack:
            # Create only category/date directories, never move or remove a directory.
            parent = root
            for part in destination.parent.relative_to(root).parts:
                parent = parent / part
                try:
                    parent.mkdir()
                except FileExistsError:
                    pass
                stack.enter_context(self.directories(parent, writable=destination.parent))
            stack.enter_context(self.directories(destination.parent, writable=destination.parent))
            stack.enter_context(self.destination_guard(destination.parent))
            with self.file(source, moving=True) as stream:
                current = fingerprint(stream)
                if current != expected or destination.parent.stat().st_dev != current['device']:
                    raise PermissionError('Identidade/volume mudou; revise o plano.')
                if any(p.name.casefold() == destination.name.casefold() for p in destination.parent.iterdir()):
                    raise FileExistsError('Destino ocupado; não será sobrescrito.')
                check()  # permissions/cancellation after locks and immediately before effect
                encoded = str(destination).encode('utf-16-le')
                class RenameInfo(ctypes.Structure):
                    _fields_ = [('flags', wintypes.DWORD), ('root', wintypes.HANDLE), ('length', wintypes.DWORD), ('name', wintypes.WCHAR * (len(encoded) // 2 + 1))]
                info = RenameInfo()
                info.flags, info.root, info.length, info.name = 0, None, len(encoded), str(destination)
                handle = msvcrt.get_osfhandle(stream.fileno())
                if not self.kernel.SetFileInformationByHandle(handle, 3, ctypes.byref(info), ctypes.sizeof(info)):
                    raise OSError(ctypes.get_last_error(), 'Movimento recusado; destino não substituído.')
                try:
                    verified = fingerprint(stream) == expected and not source.exists() and destination.exists()
                except (OSError, PermissionError):
                    verified = False
                if not verified:
                    raise RuntimeError('Movimento iniciado, mas verificação não concluída; reconciliar.')
                return current
