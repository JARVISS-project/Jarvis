import sys
from pathlib import Path

import pytest

from jarvis.integrations.google.errors import GoogleCredentialStorageCorruptError
from jarvis.integrations.google.storage import (
    ProtectedFileCredentialStore,
    WindowsDPAPIProtector,
)
from tests.integrations.google.fakes import MATERIAL


class PrefixProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return b"protected:" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"protected:"):
            raise ValueError("corrupt")
        return ciphertext.removeprefix(b"protected:")[::-1]


def test_protected_store_round_trip_has_no_plaintext_secret(tmp_path: Path) -> None:
    path = tmp_path / "credentials.bin"
    store = ProtectedFileCredentialStore(path, PrefixProtector())

    store.save(MATERIAL)

    ciphertext = path.read_bytes()
    assert b"refresh-secret" not in ciphertext
    assert b"client-secret" not in ciphertext
    assert store.load() == MATERIAL


def test_protected_store_rejects_corruption_and_delete_is_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "credentials.bin"
    path.write_bytes(b"not-protected")
    store = ProtectedFileCredentialStore(path, PrefixProtector())

    with pytest.raises(GoogleCredentialStorageCorruptError):
        store.load()
    store.delete()
    store.delete()
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI requer Windows")
def test_real_windows_dpapi_round_trip_is_user_protected(tmp_path: Path) -> None:
    path = tmp_path / "credentials.bin"
    store = ProtectedFileCredentialStore(path, WindowsDPAPIProtector())

    store.save(MATERIAL)

    ciphertext = path.read_bytes()
    assert b"refresh-secret" not in ciphertext
    assert store.load() == MATERIAL
    store.delete()
