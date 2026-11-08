"""Persistência de credenciais protegida pelo DPAPI do usuário Windows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Protocol

from jarvis.integrations.google.errors import (
    GoogleCredentialStorageCorruptError,
    GoogleCredentialStorageError,
    GoogleSecureStorageUnavailableError,
)
from jarvis.integrations.google.models import StoredCredentialMaterial


MAX_CREDENTIAL_BYTES = 64 * 1024


class DataProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...
    def unprotect(self, ciphertext: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class WindowsDPAPIProtector:
    CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise GoogleSecureStorageUnavailableError(
                "Armazenamento seguro Google requer DPAPI no Windows."
            )

    def protect(self, plaintext: bytes) -> bytes:
        return self._crypt(plaintext, protect=True)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._crypt(ciphertext, protect=False)

    def _crypt(self, value: bytes, *, protect: bool) -> bytes:
        if not value or len(value) > MAX_CREDENTIAL_BYTES:
            raise GoogleCredentialStorageError("Material de credencial inválido.")
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = ctypes.create_string_buffer(value)
        source = _DataBlob(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        destination = _DataBlob()
        description = ctypes.c_wchar_p("JARVIS Google OAuth")
        if protect:
            function = crypt32.CryptProtectData
            arguments = (
                ctypes.byref(source),
                description,
                None,
                None,
                None,
                self.CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(destination),
            )
        else:
            function = crypt32.CryptUnprotectData
            arguments = (
                ctypes.byref(source),
                None,
                None,
                None,
                None,
                self.CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(destination),
            )
        function.restype = wintypes.BOOL
        if not function(*arguments):
            raise GoogleCredentialStorageError(
                "DPAPI não conseguiu processar a credencial Google."
            )
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            kernel32.LocalFree(destination.pbData)


class ProtectedFileCredentialStore:
    def __init__(self, path: Path, protector: DataProtector) -> None:
        self.path = path
        self.protector = protector

    def load(self) -> StoredCredentialMaterial | None:
        try:
            ciphertext = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise GoogleCredentialStorageError(
                "Não foi possível ler a credencial Google protegida."
            ) from None
        if not ciphertext or len(ciphertext) > MAX_CREDENTIAL_BYTES:
            raise GoogleCredentialStorageCorruptError(
                "Credencial Google protegida está corrompida."
            )
        try:
            plaintext = self.protector.unprotect(ciphertext)
            value = json.loads(plaintext.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("object")
            return StoredCredentialMaterial.from_dict(value)
        except GoogleSecureStorageUnavailableError:
            raise
        except GoogleCredentialStorageError:
            raise GoogleCredentialStorageCorruptError(
                "Credencial Google protegida está corrompida."
            ) from None
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            raise GoogleCredentialStorageCorruptError(
                "Credencial Google protegida está corrompida."
            ) from None

    def save(self, material: StoredCredentialMaterial) -> None:
        try:
            plaintext = json.dumps(
                material.to_dict(),
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
            ciphertext = self.protector.protect(plaintext)
            if not ciphertext or len(ciphertext) > MAX_CREDENTIAL_BYTES:
                raise GoogleCredentialStorageError(
                    "Credencial Google protegida excede o limite seguro."
                )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f".{self.path.name}.{secrets.token_hex(8)}.tmp"
            )
            try:
                with temporary.open("xb") as output:
                    output.write(ciphertext)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(self.path)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        except GoogleCredentialStorageError:
            raise
        except OSError:
            raise GoogleCredentialStorageError(
                "Não foi possível persistir a credencial Google protegida."
            ) from None

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            raise GoogleCredentialStorageError(
                "Não foi possível remover a credencial Google protegida."
            ) from None
