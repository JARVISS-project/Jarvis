"""Private deterministic CLI command; deliberately absent from the LLM registry."""
from __future__ import annotations

from threading import RLock

from jarvis.application.administration import memory_version
from jarvis.security.authorization import AuthorizationReceipt
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


class MemoryForgetTool(Tool):
    name = "memory_forget"
    description = "Esquece uma memória explicitamente selecionada após confirmação."
    risk = RiskLevel.DESTRUCTIVE
    requires_authorization_receipt = True
    parameters = {"type": "object", "properties": {"id": {"type": "string"}},
                  "required": ["id"], "additionalProperties": False}

    def __init__(self, store) -> None:
        self.store = store
        self._approved = {}
        self._lock = RLock()

    def get_target(self, **kwargs) -> str:
        return "memory:" + kwargs["id"]

    def prepare(self, **kwargs) -> ToolPreparation:
        entry = next((e for e in self.store.get_all() if e.id == kwargs["id"]), None)
        if entry is None:
            raise FileNotFoundError("Memória não encontrada.")
        return ToolPreparation(self.get_target(**kwargs), (entry.id, memory_version(entry)), memory_version(entry))

    def refresh_prepared(self, preparation, **kwargs):
        return self.prepare(**kwargs)

    def bind_authorized(self, preparation, authorization=None):
        if not isinstance(authorization, AuthorizationReceipt) or not authorization.claim(
            self.name, preparation.target, preparation.binding_digest):
            raise PermissionError("Confirmação de esquecimento inválida.")
        with self._lock:
            revision = self.store.permissions.read()[1]
            self._approved = {k: v for k, v in self._approved.items() if v[1] == revision}
            if len(self._approved) >= 64:
                raise PermissionError("Limite de preparações de esquecimento.")
            self._approved[id(preparation)] = (preparation, revision)
        return preparation

    def execute_authorized(self, preparation) -> str:
        with self._lock:
            approved = self._approved.pop(id(preparation), None)
            if approved is None or approved[0] is not preparation or approved[1] != self.store.permissions.read()[1]:
                raise PermissionError("Confirmação já utilizada ou inválida.")
            memory_id, version = preparation.context
            if preparation.target != "memory:" + memory_id:
                raise PermissionError("Alvo de esquecimento alterado.")
            current = self.prepare(id=memory_id)
            if current.binding_digest != version:
                raise PermissionError("Memória mudou após o preparo.")
            if not self.store.delete(memory_id) or any(e.id == memory_id for e in self.store.get_all()):
                raise RuntimeError("Esquecimento não verificado.")
            return "Memória esquecida e retirada dos contextos gerenciados. Backups e cópias externas já enviadas podem permanecer."

    def execute(self, **kwargs):
        raise PermissionError("Esquecimento exige executor e confirmação vinculada.")
