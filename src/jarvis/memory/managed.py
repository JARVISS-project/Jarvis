"""Memory controls shared by all runtime channels, without operational authority."""
from __future__ import annotations

import json
import re
from threading import RLock
from typing import Callable

from jarvis.memory.store import MemoryStore

_SECRET = re.compile(
    r"(?:-----BEGIN [^-]*(?:PRIVATE KEY)|\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|senha|client[_ -]?secret|credential|credencial)[\"']?\s*(?:[:=]|é\b|is\b)\s*\S+|\bBearer\s+\S+|\b(?:sk-|ghp_|ya29\.)[a-zA-Z0-9_-]{12,}|\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


def validate_memory(content: str, metadata: dict | None = None) -> None:
    if not isinstance(content, str) or not 0 < len(content.strip()) <= 12000:
        raise ValueError("Conteúdo ausente ou acima do limite de 12000 caracteres.")
    if _SECRET.search(content + " " + json.dumps(metadata or {}, ensure_ascii=False)):
        raise ValueError("Credenciais identificadas: use o armazenamento de segredos, não a memória.")


class ManagedMemoryStore(MemoryStore):
    def __init__(self, store: MemoryStore, permissions) -> None:
        self.store = store
        self.permissions = permissions
        self.temporary = False
        self.context_epoch = 0
        self.on_mutation: Callable[[], None] | None = None
        self._lock = RLock()

    def _saving(self, resource: str = "memory.save") -> None:
        if self.temporary:
            raise PermissionError("Sessão temporária: novas gravações de memória estão bloqueadas.")
        self.permissions.require(resource)

    def add(self, content: str, category: str, **kwargs):
        with self.permissions.resource_effect("memory.save"), self._lock:
            self._saving()
            validate_memory(content, kwargs.get("metadata"))
            # Consent to local context never implies external sharing.
            metadata = dict(kwargs.pop("metadata", None) or {})
            metadata.setdefault("share_external", False)
            kwargs["metadata"] = metadata
            entry = self.store.add(content, category, **kwargs)
            self._changed()
            return entry

    def get_all(self):
        return self.store.get_all()

    def get_relevant(self, query: str, max_results: int = 5, min_score: float = 0.3):
        if self.permissions.decision("memory.context").mode != "allow":
            return []
        return self.store.get_relevant(query, max_results, min_score)

    def get_context(self, query: str, *, external: bool = False):
        entries = self.get_relevant(query)
        if external:
            if self.permissions.decision("memory.external", default="block").mode != "allow":
                return []
            entries = [e for e in entries if e.metadata.get("share_external") is True]
        return entries

    def update(self, memory_id: str, **kwargs):
        with self.permissions.resource_effect("memory.edit"), self._lock:
            self._saving("memory.edit")
            expected = kwargs.pop("expected_version", None)
            if expected is not None:
                from jarvis.application.administration import memory_version
                current = next((e for e in self.store.get_all() if e.id == memory_id), None)
                if current is None or memory_version(current) != expected:
                    raise PermissionError("Memória alterada antes da gravação.")
            validate_memory(kwargs.get("content"), kwargs.get("metadata"))
            result = self.store.update(memory_id, **kwargs)
            if not result:
                raise PermissionError("Memória não encontrada durante a edição.")
            self._changed()
            return result

    def delete(self, memory_id: str) -> bool:
        # Forgetting remains available even when saving is disabled.
        with self.permissions._lock, self._lock:
            if self.permissions.decision("memory.delete", default="ask").mode == "block":
                raise PermissionError("Esquecimento bloqueado na Central de Permissões.")
            # Callers must supply their own bound confirmation; this is storage.
            # The private CLI Tool already owns the effect mutex.
            result = self.store.delete(memory_id)
            self._changed()
            return result

    def update_consent(self, memory_id: str, consent: bool) -> bool:
        if consent:
            with self.permissions.resource_effect("memory.edit"), self._lock:
                self._saving("memory.edit")
                result = self.store.update_consent(memory_id, consent)
        else:
            with self.permissions._lock, self._lock:
                result = self.store.update_consent(memory_id, consent)
        self._changed()
        return result

    def set_temporary(self, temporary: bool) -> None:
        with self._lock:
            self.temporary = temporary
            self._changed()

    def clear(self) -> None:
        with self._lock:
            self.store.clear()
            self._changed()

    def cleanup_expired(self) -> int:
        count = self.store.cleanup_expired()
        if count:
            self._changed()
        return count

    def invalidate_context(self) -> None:
        with self._lock:
            self.context_epoch += 1

    def _changed(self) -> None:
        self.context_epoch += 1
        if self.on_mutation is not None:
            self.on_mutation()
