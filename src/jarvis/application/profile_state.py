"""Bounded atomic profile state, encrypted before any filesystem write."""
from copy import deepcopy
import json
import os
import tempfile
import zlib

from jarvis.integrations.google.storage import WindowsDPAPIProtector


class ProtectedProfileState:
    def __init__(self, path, protector=None):
        self.path = path
        self.protector = protector

    def read(self, default):
        if not self.path.exists():
            return deepcopy(default)
        try:
            raw = self.path.read_bytes()
            if len(raw) > 65536:
                raise ValueError
            compressed = (self.protector or WindowsDPAPIProtector()).unprotect(raw)
            decoder = zlib.decompressobj()
            data = decoder.decompress(compressed, 1048577)
            if len(data) > 1048576 or not decoder.eof or decoder.unused_data:
                raise ValueError
            return json.loads(data)
        except Exception:
            raise PermissionError('Estado protegido indisponível; automações e envios bloqueados.') from None

    def write(self, value):
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if len(data) > 1048576:
            raise ValueError('Limite do estado local atingido.')
        data = zlib.compress(data)
        if len(data) > 60000:
            raise ValueError('Limite do armazenamento protegido atingido.')
        raw = (self.protector or WindowsDPAPIProtector()).protect(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
