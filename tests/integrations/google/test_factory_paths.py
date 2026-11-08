from pathlib import Path

from jarvis.integrations.google import factory


def test_google_credential_dir_prefers_explicit_env(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "stable-google"
    monkeypatch.setenv("JARVIS_GOOGLE_CREDENTIAL_DIR", str(target))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert factory.google_credential_dir() == target


def test_default_path_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("JARVIS_GOOGLE_CREDENTIAL_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    resolved = factory._resolve_credential_path("credentials.bin", None)

    assert resolved == tmp_path / "JARVIS" / "google" / "credentials.bin"


def test_legacy_relative_credential_is_migrated_atomically(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stable_root = tmp_path / "stable"
    working = tmp_path / "working"
    legacy = working / "data" / "google" / "credentials.bin"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"dpapi-ciphertext")

    monkeypatch.setenv("JARVIS_GOOGLE_CREDENTIAL_DIR", str(stable_root))
    monkeypatch.chdir(working)

    resolved = factory._resolve_credential_path("credentials.bin", None)

    assert resolved == stable_root / "credentials.bin"
    assert resolved.read_bytes() == b"dpapi-ciphertext"
    assert legacy.read_bytes() == b"dpapi-ciphertext"


def test_existing_stable_credential_wins_over_legacy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stable_root = tmp_path / "stable"
    stable_root.mkdir()
    stable = stable_root / "credentials.bin"
    stable.write_bytes(b"fresh")
    working = tmp_path / "working"
    legacy = working / "data" / "google" / "credentials.bin"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"old")

    monkeypatch.setenv("JARVIS_GOOGLE_CREDENTIAL_DIR", str(stable_root))
    monkeypatch.chdir(working)

    resolved = factory._resolve_credential_path("credentials.bin", None)

    assert resolved.read_bytes() == b"fresh"
