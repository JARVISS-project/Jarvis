"""Pedido tipado, independente do meio usado para confirmar."""

from dataclasses import dataclass
from enum import Enum

from jarvis.security.risk import RiskLevel


class ConfirmationPurpose(Enum):
    SECURITY = "security"
    TARGET = "target"


@dataclass(frozen=True)
class ConfirmationRequest:
    action: str
    target: str
    risk: RiskLevel
    prompt_text: str
    purpose: ConfirmationPurpose = ConfirmationPurpose.SECURITY
    consequence: str | None = None
    parameters: dict | None = None
