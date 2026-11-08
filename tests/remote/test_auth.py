from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from jarvis.remote.auth import (
    Device,
    DeviceRegistry,
    DeviceRegistryError,
    PairingManager,
    TokenAuthenticator,
    hash_token,
)


def make_device(device_id: str = "dev-1", token: str = "secret") -> Device:
    now = datetime.now(timezone.utc).isoformat()
    return Device(
        id=device_id,
        name="Notebook",
        created_at=now,
        last_seen=now,
        token_hash=hash_token(token),
    )


def test_device_registry_persists_and_authenticates(tmp_path: Path) -> None:
    path = tmp_path / "remote" / "devices.json"
    registry = DeviceRegistry(path)
    registry.add(make_device())

    reloaded = DeviceRegistry(path)

    assert reloaded.get("dev-1") is not None
    assert TokenAuthenticator(reloaded).authenticate("dev-1", "secret") is not None
    assert TokenAuthenticator(reloaded).authenticate("dev-1", "wrong") is None


def test_revocation_is_durable_and_never_cleaned(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    registry = DeviceRegistry(path)
    registry.add(make_device())

    assert registry.revoke("dev-1") is True
    assert registry.revoke("dev-1") is True

    reloaded = DeviceRegistry(path)
    assert reloaded.is_revoked("dev-1") is True
    assert TokenAuthenticator(reloaded).authenticate("dev-1", "secret") is None
    assert reloaded.list_devices()[0].revoked is True


def test_registry_detects_external_revocation_on_refresh(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    gateway_registry = DeviceRegistry(path)
    gateway_registry.add(make_device())
    command_registry = DeviceRegistry(path)
    command_registry.revoke("dev-1")

    assert gateway_registry.is_revoked("dev-1") is False
    gateway_registry.refresh()
    assert gateway_registry.is_revoked("dev-1") is True


def test_registry_fails_closed_on_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(DeviceRegistryError):
        DeviceRegistry(path)


def test_pairing_is_shared_between_local_and_gateway_processes(tmp_path: Path) -> None:
    path = tmp_path / "pairing.json"
    local_manager = PairingManager(path)
    challenge = local_manager.create_pairing("Phone")
    persisted = path.read_text(encoding="utf-8")

    assert challenge.pairing_code not in persisted
    assert "code_hash" in persisted

    gateway_manager = PairingManager(path)
    assert gateway_manager.consume_pairing(challenge.pairing_code, "Phone") == "Phone"
    assert gateway_manager.consume_pairing(challenge.pairing_code, "Phone") is None
    assert path.exists() is False


def test_pairing_rejects_name_mismatch_without_consuming_code(tmp_path: Path) -> None:
    path = tmp_path / "pairing.json"
    manager = PairingManager(path)
    challenge = manager.create_pairing("Phone")

    assert manager.consume_pairing(challenge.pairing_code, "Attacker") is None
    assert manager.consume_pairing(challenge.pairing_code, "Phone") == "Phone"


def test_pairing_expires_and_is_removed(tmp_path: Path) -> None:
    now = [100.0]
    path = tmp_path / "pairing.json"
    manager = PairingManager(path, clock=lambda: now[0])
    challenge = manager.create_pairing("Phone")
    now[0] = challenge.expires_at

    assert manager.consume_pairing(challenge.pairing_code, "Phone") is None
    assert path.exists() is False


def test_registry_rejects_unknown_persisted_fields(tmp_path: Path) -> None:
    path = tmp_path / "devices.json"
    payload = make_device().to_dict()
    payload["admin"] = True
    path.write_text(json.dumps([payload]), encoding="utf-8")

    with pytest.raises(DeviceRegistryError):
        DeviceRegistry(path)
