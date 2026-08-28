"""Comparação local de tela sem expor pixels ou fingerprints."""

from dataclasses import dataclass, field
from enum import Enum
from hmac import compare_digest
from hashlib import sha256
from threading import RLock
import time
from typing import Callable
from uuid import uuid4

from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.vision.backend import VisionBackend


class ScreenChange(str, Enum):
    BASELINE = "baseline"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class ScreenChangeResult:
    status: ScreenChange
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class VisualVerificationToken:
    id: str
    width: int
    height: int
    created_at: float = field(repr=False)
    target_digest: bytes = field(repr=False)
    fingerprint: bytes = field(repr=False)


class VisionService:
    def __init__(
        self,
        backend: VisionBackend,
        capability_policy: CapabilityPolicy,
        *,
        max_pixels: int = 33_177_600,
        verification_max_age_seconds: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_pixels <= 0:
            raise ValueError("max_pixels deve ser positivo.")
        if verification_max_age_seconds <= 0:
            raise ValueError("verification_max_age_seconds deve ser positivo.")
        self._backend = backend
        self.capability_policy = capability_policy
        self.max_pixels = max_pixels
        self.verification_max_age_seconds = verification_max_age_seconds
        self.monotonic = monotonic
        self._lock = RLock()
        self._last_fingerprint: bytes | None = None
        self._pending: dict[str, VisualVerificationToken] = {}

    def observe_change(self) -> ScreenChangeResult:
        self.capability_policy.require(Capability.VISION)
        with self._lock:
            current, width, height = self._capture_fingerprint_locked()
            previous = self._last_fingerprint
            self._last_fingerprint = current
        if previous is None:
            status = ScreenChange.BASELINE
        elif previous == current:
            status = ScreenChange.UNCHANGED
        else:
            status = ScreenChange.CHANGED
        return ScreenChangeResult(status, width, height)

    def begin_verification(self, target_identity: str) -> VisualVerificationToken:
        self.capability_policy.require(Capability.VISION)
        target = target_identity.strip()
        if not target:
            raise ValueError("Identidade visual do alvo ausente.")
        now = self.monotonic()
        with self._lock:
            self._discard_expired_locked(now)
            fingerprint, width, height = self._capture_fingerprint_locked()
            token = VisualVerificationToken(
                id=str(uuid4()),
                width=width,
                height=height,
                created_at=now,
                target_digest=sha256(target.encode("utf-8")).digest(),
                fingerprint=fingerprint,
            )
            self._pending[token.id] = token
            return token

    def finish_verification(
        self,
        token: VisualVerificationToken,
        target_identity: str,
    ) -> ScreenChangeResult:
        self.capability_policy.require(Capability.VISION)
        now = self.monotonic()
        target_digest = sha256(target_identity.strip().encode("utf-8")).digest()
        with self._lock:
            stored = self._pending.pop(token.id, None)
            if stored != token:
                raise ValueError("Baseline visual ausente ou já consumida.")
            if now - token.created_at > self.verification_max_age_seconds:
                raise TimeoutError("Baseline visual expirada.")
            if not compare_digest(token.target_digest, target_digest):
                raise ValueError("Identidade visual do alvo não corresponde.")
            current, width, height = self._capture_fingerprint_locked()
        status = (
            ScreenChange.UNCHANGED
            if current == token.fingerprint
            else ScreenChange.CHANGED
        )
        return ScreenChangeResult(status, width, height)

    def cancel_verification(self, token: VisualVerificationToken) -> None:
        with self._lock:
            self._pending.pop(token.id, None)

    def _capture_fingerprint_locked(self) -> tuple[bytes, int, int]:
        frame = self._backend.capture_screen()
        if frame.width <= 0 or frame.height <= 0:
            raise RuntimeError("A captura de tela possui dimensões inválidas.")
        pixel_count = frame.width * frame.height
        if pixel_count > self.max_pixels:
            raise RuntimeError("A captura de tela excede o limite de pixels.")
        if frame.mode != "RGB":
            raise RuntimeError("Formato de captura de tela não permitido.")
        expected_bytes = pixel_count * 3
        if len(frame.pixels) != expected_bytes:
            raise RuntimeError("O frame de tela possui tamanho inconsistente.")
        fingerprint = sha256()
        fingerprint.update(frame.width.to_bytes(4, "big"))
        fingerprint.update(frame.height.to_bytes(4, "big"))
        fingerprint.update(frame.pixels)
        return fingerprint.digest(), frame.width, frame.height

    def _discard_expired_locked(self, now: float) -> None:
        expired = [
            token_id
            for token_id, token in self._pending.items()
            if now - token.created_at > self.verification_max_age_seconds
        ]
        for token_id in expired:
            self._pending.pop(token_id, None)
