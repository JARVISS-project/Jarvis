"""Local discovery is evidence, never authority to launch an application."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import struct
from threading import RLock

from jarvis.desktop.catalog import _normalize


@dataclass(frozen=True)
class DiscoveredApplication:
    id: str
    name: str
    source: str
    locator: str = field(repr=False)
    target: str = field(repr=False)
    fingerprint: str
    kind: str = "application"
    available: bool = True
    reason: str = ""
    legacy: str | None = None

    @property
    def grant_key(self) -> str:
        return self.id + "|" + self.fingerprint


def opaque_id(source: str, locator: str) -> str:
    return "app-" + hashlib.sha256((source + "|" + locator.casefold()).encode()).hexdigest()[:32]


def file_identity(path: Path) -> str:
    """Bind the resolved file, binary bytes and Windows file identity."""
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError("Destino ausente ou sem identidade de arquivo local.")
    import stat
    if any(p.exists() and (p.is_symlink() or getattr(p.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
           for p in (path, *path.parents)):
        raise ValueError("Destino com link/junction indisponível.")
    before = path.stat()
    if before.st_nlink != 1 or before.st_size > 512 * 1024 * 1024:
        raise ValueError("Identidade de arquivo não suportada.")
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if before != path.stat():
        raise ValueError("Arquivo mudou durante validação.")
    return hashlib.sha256(f"{path.resolve()}|{before.st_dev}|{before.st_ino}|{digest.hexdigest()}".casefold().encode()).hexdigest()


def executable_kind(path: Path) -> str:
    """A .exe suffix is not enough: accept only GUI PE images, no shells/installers."""
    if path.suffix.casefold() != '.exe' or not path.is_absolute():
        return 'unsupported'
    stem = _normalize(path.stem)
    if any(word in stem.split() for word in ('setup', 'install', 'installer', 'uninstall', 'unins', 'update', 'updater')) or stem.startswith('unins'):
        return 'installer'
    if stem in {'cmd', 'powershell', 'pwsh', 'wscript', 'cscript', 'rundll32', 'regsvr32', 'mshta', 'msiexec', 'python', 'pythonw', 'node', 'bash', 'wsl', 'conhost', 'wt'}:
        return 'command_line'
    try:
        with path.open('rb') as stream:
            header = stream.read(64)
            if header[:2] != b'MZ' or len(header) != 64:
                return 'unsupported'
            offset = struct.unpack_from('<I', header, 60)[0]
            if offset > 1024 * 1024:
                return 'unsupported'
            stream.seek(offset)
            pe = stream.read(96)
            if pe[:4] != b'PE\0\0' or len(pe) < 94:
                return 'unsupported'
            if struct.unpack_from('<H', pe, 22)[0] & 0x2000 or struct.unpack_from('<H', pe, 24)[0] not in {0x10b, 0x20b}:
                return 'unsupported'
            subsystem = struct.unpack_from('<H', pe, 24 + 68)[0]
            return 'application' if subsystem == 2 else 'command_line'
    except OSError:
        return 'unavailable'


class ApplicationCatalog:
    def __init__(self, discovery) -> None:
        self.discovery = discovery
        self._entries: dict[str, DiscoveredApplication] = {}
        self._lock = RLock()
        self.revision = 0
        self.diagnostics: tuple[str, ...] = ()

    def refresh(self) -> list[DiscoveredApplication]:
        entries, diagnostics = self.discovery.discover()
        if len(entries) > 5000:
            raise RuntimeError("Catálogo excede o limite local; nenhuma autorização ampliada.")
        with self._lock:
            indexed = {entry.id: entry for entry in entries}
            if len(indexed) != len(entries):
                raise ValueError("Identidades duplicadas na descoberta.")
            self._entries = indexed
            self.diagnostics = tuple(diagnostics)
            self.revision += 1
            return self.entries()

    def seed(self, entries):
        with self._lock:
            for entry in entries:
                self._entries.setdefault(entry.id, entry)

    def entries(self) -> list[DiscoveredApplication]:
        with self._lock:
            return sorted(self._entries.values(), key=lambda entry: (entry.name.casefold(), entry.id))

    def get(self, application_id: str, *, validate: bool = True) -> DiscoveredApplication:
        with self._lock:
            entry = self._entries.get(application_id)
        if entry is None or not entry.available:
            raise PermissionError("Aplicativo não descoberto ou indisponível; atualize o catálogo.")
        if validate and self.discovery.revalidate(entry) != entry.fingerprint:
            raise PermissionError("Destino/identidade mudou; atualize e autorize novamente.")
        return entry

    def match(self, name: str, aliases: dict[str, str] | None = None) -> list[DiscoveredApplication]:
        value = _normalize(name)
        alias = (aliases or {}).get(value)
        matches = [entry for entry in self.entries() if _normalize(entry.name) == value or entry.legacy == value or entry.id == alias]
        # Different discovery sources for the exact same executable are compatible.
        unique = {}
        for entry in matches:
            unique.setdefault((entry.target.casefold(), entry.fingerprint), entry)
        return list(unique.values())
