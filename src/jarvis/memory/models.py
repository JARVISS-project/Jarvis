from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any
import unicodedata
from uuid import uuid4


class MemorySensitivity(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class MemoryProvenance(str, Enum):
    USER_EXPLICIT = "user_explicit"
    CONVERSATION_DERIVED = "conversation_derived"
    TOOL_OBSERVATION = "tool_observation"


def memory_tokens(text: str) -> set[str]:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return set(re.findall(r"[a-z0-9]{3,}", plain))


def memory_relevance(entry: "MemoryEntry", query_terms: set[str]) -> float:
    all_terms = memory_tokens(entry.content) | memory_tokens(entry.category)
    if not all_terms:
        return 0.0
    intersection = query_terms & all_terms
    return len(intersection) / len(query_terms | all_terms)


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    content: str
    category: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sensitivity: MemorySensitivity = MemorySensitivity.PRIVATE
    provenance: MemoryProvenance = MemoryProvenance.USER_EXPLICIT
    retention_days: int | None = None
    consent_given: bool = True
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("conteúdo ausente")
        if not self.category.strip():
            raise ValueError("categoria ausente")
        if self.retention_days is not None and self.retention_days <= 0:
            raise ValueError("retenção deve ser maior que zero")
        if not isinstance(self.consent_given, bool):
            raise ValueError("consentimento inválido")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadados inválidos")

    def is_expired(self) -> bool:
        if self.retention_days is None:
            return False
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created_at
        return age.days >= self.retention_days
