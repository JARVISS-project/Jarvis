"""Pairing local, autenticação e registro persistente de dispositivos."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Mapping

from jarvis.security.audit import AuditLogger


PAIRING_CODE_LENGTH = 6
PAIRING_CODE_TTL_SECONDS = 300
TOKEN_LENGTH = 32
DEVICE_REGISTRY_PATH = Path("data/remote_devices.json")
PAIRING_STATE_PATH = Path("data/remote_pairing.json")
DEFAULT_PERMISSIONS = ("chat", "state", "telemetry", "confirmation")


class DeviceRegistryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    created_at: str
    last_seen: str
    token_hash: str = field(repr=False)
    revoked: bool = False
    permissions: tuple[str, ...] = DEFAULT_PERMISSIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "token_hash": self.token_hash,
            "revoked": self.revoked,
            "permissions": list(self.permissions),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Device:
        expected = {
            "id", "name", "created_at", "last_seen", "token_hash",
            "revoked", "permissions",
        }
        if set(data) - expected:
            raise ValueError("Campos inesperados no dispositivo")
        required = {"id", "name", "created_at", "last_seen", "token_hash"}
        if required - set(data):
            raise ValueError("Campos ausentes no dispositivo")
        permissions = data.get("permissions", list(DEFAULT_PERMISSIONS))
        if not isinstance(permissions, list) or not all(
            isinstance(item, str) and item in DEFAULT_PERMISSIONS
            for item in permissions
        ):
            raise ValueError("Permissões inválidas")
        values = [
            data["id"], data["name"], data["created_at"],
            data["last_seen"], data["token_hash"],
        ]
        if not all(isinstance(value, str) and value for value in values):
            raise ValueError("Dados de dispositivo inválidos")
        revoked = data.get("revoked", False)
        if not isinstance(revoked, bool):
            raise ValueError("Estado de revogação inválido")
        return cls(
            id=data["id"],
            name=data["name"],
            created_at=data["created_at"],
            last_seen=data["last_seen"],
            token_hash=data["token_hash"],
            revoked=revoked,
            permissions=tuple(dict.fromkeys(permissions)),
        )


@dataclass(frozen=True, slots=True)
class PairingChallenge:
    pairing_code: str = field(repr=False)
    device_name: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class PendingPairing:
    code_hash: str = field(repr=False)
    device_name: str
    created_at: float
    expires_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "code_hash": self.code_hash,
            "device_name": self.device_name,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


def generate_pairing_code() -> str:
    return "".join(
        secrets.choice("0123456789") for _ in range(PAIRING_CODE_LENGTH)
    )


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_LENGTH)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _audit(
    logger: AuditLogger | None,
    event: str,
    target: str,
    authorized: bool,
    status: str,
) -> None:
    if logger is not None:
        logger.log("remote_gateway", f"{event}:{target}", authorized, status)


class DeviceRegistry:
    """Registro com escrita atômica e tombstones duráveis de revogação."""

    def __init__(
        self,
        path: Path = DEVICE_REGISTRY_PATH,
        audit: AuditLogger | None = None,
    ) -> None:
        self.path = path
        self.audit = audit
        self._lock = threading.RLock()
        self._devices = self._load()

    def _load(self) -> dict[str, Device]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Registro deve ser uma lista")
            devices = [Device.from_dict(item) for item in raw]
            if len({device.id for device in devices}) != len(devices):
                raise ValueError("IDs de dispositivo duplicados")
            return {device.id: device for device in devices}
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise DeviceRegistryError(
                f"Registro de dispositivos remoto inválido: {self.path}"
            ) from error

    def _save(self, devices: Mapping[str, Device]) -> None:
        _atomic_write_json(
            self.path,
            [device.to_dict() for device in devices.values()],
        )

    def add(self, device: Device) -> None:
        with self._lock:
            if device.id in self._devices:
                raise ValueError("Dispositivo já existe")
            updated = {**self._devices, device.id: device}
            self._save(updated)
            self._devices = updated
        _audit(self.audit, "device_paired", device.id, True, "success")

    def get(self, device_id: str) -> Device | None:
        with self._lock:
            return self._devices.get(device_id)

    def list_devices(self) -> list[Device]:
        with self._lock:
            return list(self._devices.values())

    def refresh(self) -> None:
        """Recarrega mudanças feitas por comandos locais em outro processo."""
        with self._lock:
            self._devices = self._load()

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return False
            if device.revoked:
                return True
            updated = {
                **self._devices,
                device_id: replace(device, revoked=True),
            }
            self._save(updated)
            self._devices = updated
        _audit(self.audit, "device_revoked", device_id, True, "success")
        return True

    def update_last_seen(self, device_id: str) -> None:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None or device.revoked:
                return
            updated = {
                **self._devices,
                device_id: replace(
                    device,
                    last_seen=datetime.now(timezone.utc).isoformat(),
                ),
            }
            self._save(updated)
            self._devices = updated

    def is_revoked(self, device_id: str) -> bool:
        with self._lock:
            device = self._devices.get(device_id)
            return device is not None and device.revoked


class PairingManager:
    """Armazena um desafio local one-time apenas como hash."""

    def __init__(
        self,
        path: Path = PAIRING_STATE_PATH,
        audit: AuditLogger | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.audit = audit
        self._clock = clock
        self._lock = threading.RLock()

    def create_pairing(self, device_name: str) -> PairingChallenge:
        name = device_name.strip()
        if not name or len(name) > 80:
            raise ValueError("Nome de dispositivo inválido")
        now = self._clock()
        code = generate_pairing_code()
        pending = PendingPairing(
            code_hash=hash_token(code),
            device_name=name,
            created_at=now,
            expires_at=now + PAIRING_CODE_TTL_SECONDS,
        )
        with self._lock:
            _atomic_write_json(self.path, pending.to_dict())
        _audit(self.audit, "pairing_created", "local", True, "success")
        return PairingChallenge(code, name, pending.expires_at)

    def consume_pairing(
        self,
        pairing_code: str,
        device_name: str,
    ) -> str | None:
        if (
            len(pairing_code) != PAIRING_CODE_LENGTH
            or not pairing_code.isascii()
            or not pairing_code.isdigit()
        ):
            _audit(self.audit, "pairing_consumed", "remote", False, "invalid")
            return None
        with self._lock:
            pending = self._load_pending()
            if pending is None:
                _audit(self.audit, "pairing_consumed", "remote", False, "missing")
                return None
            if pending.is_expired(self._clock()):
                self._remove_pending()
                _audit(self.audit, "pairing_consumed", "remote", False, "expired")
                return None
            if not hmac.compare_digest(hash_token(pairing_code), pending.code_hash):
                _audit(self.audit, "pairing_consumed", "remote", False, "invalid")
                return None
            if not hmac.compare_digest(device_name, pending.device_name):
                _audit(self.audit, "pairing_consumed", "remote", False, "name_mismatch")
                return None
            self._remove_pending()
        _audit(self.audit, "pairing_consumed", "remote", True, "success")
        return pending.device_name

    def cleanup_expired(self) -> int:
        with self._lock:
            pending = self._load_pending()
            if pending is None or not pending.is_expired(self._clock()):
                return 0
            self._remove_pending()
            return 1

    def _load_pending(self) -> PendingPairing | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != {
                "code_hash", "device_name", "created_at", "expires_at"
            }:
                raise ValueError
            code_hash = data["code_hash"]
            device_name = data["device_name"]
            created_at = data["created_at"]
            expires_at = data["expires_at"]
            if (
                not isinstance(code_hash, str)
                or len(code_hash) != 64
                or not isinstance(device_name, str)
                or not device_name
                or isinstance(created_at, bool)
                or not isinstance(created_at, (int, float))
                or isinstance(expires_at, bool)
                or not isinstance(expires_at, (int, float))
            ):
                raise ValueError
            return PendingPairing(
                code_hash=code_hash,
                device_name=device_name,
                created_at=float(created_at),
                expires_at=float(expires_at),
            )
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise DeviceRegistryError(
                f"Estado de pairing remoto inválido: {self.path}"
            ) from error

    def _remove_pending(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            raise DeviceRegistryError(
                "Não foi possível consumir o desafio de pairing"
            ) from error


class TokenAuthenticator:
    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry

    def authenticate(self, device_id: str, token: str) -> Device | None:
        device = self.registry.get(device_id)
        if device is None or device.revoked:
            return None
        if not verify_token(token, device.token_hash):
            return None
        self.registry.update_last_seen(device_id)
        return device
