"""Local administrative restrictions. Never a replacement for Tool authority."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Callable, Iterator

from jarvis.integrations.google.operation_lock import GoogleOperationLock
from jarvis.integrations.google.errors import GoogleOperationInProgressError
from jarvis.security.capabilities import TOOL_CAPABILITIES

MODES = {"block", "ask", "allow"}
MEMORY_ACTIONS = {"memory.save", "memory.edit", "memory.delete", "memory.context", "memory.external"}
KNOWN_RESOURCES = {f"tool:{name}" for name in TOOL_CAPABILITIES} | MEMORY_ACTIONS | {
    "microphone.capture", "model.external", "delegation.execute",
    "capture.screen",
}
PATH_ACTIONS = {"read", "write", "delete"}
SCOPE_PARAMETERS = {"application", "destination", "calendar_id", "organization"}
_EFFECT_CONTEXT = ContextVar("jarvis_permission_effect", default=None)


def indirect_tool(name: str) -> bool:
    return name.startswith(("desktop_", "browser_", "vision_", "safe_", "local_application_", "app_catalog_")) or name in {
        "obsidian_open_note", "current_media", "local_media_control", "local_volume_control",
    }


def scoped_resource(tool_name: str, parameter: str, value: str) -> str:
    return f"tool:{tool_name}@{parameter}:" + hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class PermissionDecision:
    mode: str
    revision: str


class PermissionStore:
    """Versioned, atomic overlay; missing file preserves existing authority.

    Invalid files block every managed operation. Denies survive conflicting
    allows, including lexical and resolved aliases of a path. Local OS account
    administrators are trusted; this is not an arbitrary-code sandbox.
    """
    def __init__(self, path: Path, roots: tuple[Path, ...] = ()) -> None:
        self.path = path.resolve()
        self.protected_paths = {self.path}
        self.roots = tuple(root.resolve() for root in roots)
        self._lock = RLock()
        self._last_revision: str | None = None
        self.on_change: Callable[[], None] | None = None

    @classmethod
    def for_config(cls, config) -> PermissionStore:
        roots = tuple(p for p in (config.filesystem_base_directory, config.obsidian_vault_path) if p is not None)
        return cls(config.settings_path.parent / "permissions.json", roots)

    @staticmethod
    def _pairs(pairs: list[tuple]) -> dict:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate key")
        return result

    def read(self) -> tuple[list[dict], str, bool]:
        with self._lock:
            missing = False
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                raw = b""
                missing = True
            except OSError:
                raw = b"<unreadable>"
            revision = hashlib.sha256(raw).hexdigest()
            valid = missing or bool(raw)
            rules: list[dict] = []
            if raw:
                try:
                    if len(raw) > 262144:
                        raise ValueError
                    value = json.loads(raw, object_pairs_hook=self._pairs)
                    if set(value) != {"schema", "rules"} or type(value["schema"]) is not int or value["schema"] != 1:
                        raise ValueError
                    rules = value["rules"]
                    if not isinstance(rules, list) or len(rules) > 1024:
                        raise ValueError
                    for rule in rules:
                        self._validate(rule)
                except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                    rules, valid = [], False
            changed = self._last_revision is not None and self._last_revision != revision
            self._last_revision = revision
        if changed and self.on_change is not None:
            self.on_change()
        return rules, revision, valid

    def _validate(self, rule: object) -> None:
        if not isinstance(rule, dict) or set(rule) != {"resource", "action", "mode", "expires", "origin"}:
            raise ValueError("Regra de permissão inválida.")
        resource = rule["resource"]
        if not isinstance(resource, str) or len(resource) > 4096:
            raise ValueError("Recurso inválido.")
        if resource.startswith("path:"):
            path = Path(resource[5:])
            if not path.is_absolute() or not any(path.resolve().is_relative_to(root) for root in self.roots):
                raise ValueError("Caminho fora do escopo configurado.")
            if rule["action"] not in PATH_ACTIONS:
                raise ValueError("Ação inválida.")
        else:
            scoped = False
            if "@" in resource:
                base, selector = resource.split("@", 1)
                parameter, _, digest = selector.partition(":")
                scoped = base in KNOWN_RESOURCES and parameter in SCOPE_PARAMETERS and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
            if (resource not in KNOWN_RESOURCES and not scoped) or rule["action"] != "use":
                raise ValueError("Recurso/ação não suportado.")
        if rule["mode"] not in MODES or rule["origin"] != "user_admin":
            raise ValueError("Concessão inválida.")
        expires = rule["expires"]
        if expires is not None:
            if not isinstance(expires, str) or datetime.fromisoformat(expires).tzinfo is None:
                raise ValueError("Validade exige data absoluta com timezone.")

    @staticmethod
    def _active(rule: dict) -> bool:
        return rule["expires"] is None or datetime.fromisoformat(rule["expires"]) > datetime.now(timezone.utc)

    def decision(self, resource: str, action: str = "use", *, default: str = "allow") -> PermissionDecision:
        rules, revision, valid = self.read()
        if not valid:
            return PermissionDecision("block", revision)
        matches = [r for r in rules if r["resource"] == resource and r["action"] == action and self._active(r)]
        modes = {r["mode"] for r in matches}
        return PermissionDecision("block" if "block" in modes else "ask" if "ask" in modes else "allow" if "allow" in modes else default, revision)

    def require(self, resource: str, *, default: str = "allow") -> str:
        decision = self.decision(resource, default=default)
        if decision.mode != "allow":
            raise PermissionError("Acesso bloqueado ou exige confirmação administrativa explícita.")
        return decision.revision

    def path_decision(self, path: Path, action: str) -> str:
        rules, _, valid = self.read()
        if not valid:
            return "block"
        aliases = (path.absolute(), path.resolve())
        modes = set()
        for rule in rules:
            if not rule["resource"].startswith("path:") or rule["action"] != action or not self._active(rule):
                continue
            parent = Path(rule["resource"][5:])
            if any(alias.is_relative_to(p) for alias in aliases for p in (parent.absolute(), parent.resolve())):
                modes.add(rule["mode"])
        return "block" if "block" in modes else "ask" if "ask" in modes else "allow"

    def has_path_restrictions(self) -> bool:
        rules, _, valid = self.read()
        return not valid or any(r["resource"].startswith("path:") and r["mode"] != "allow" and self._active(r) for r in rules)

    def require_operation(self, tool_name: str, path: Path, action: str) -> None:
        context = _EFFECT_CONTEXT.get()
        if tool_name == "obsidian_list_notes" and context is not None and context[0] is self and context[1] == "obsidian_search_notes":
            tool_name = "obsidian_search_notes"
        mode = self.decision(f"tool:{tool_name}").mode
        path_mode = self.path_decision(path, action)
        if "block" in {mode, path_mode}:
            raise PermissionError("Recurso bloqueado na Central de Permissões.")
        if "ask" in {mode, path_mode}:
            compatible = {tool_name}
            if tool_name == "obsidian_list_notes":
                compatible.add("obsidian_search_notes")
            if context is None or context[0] is not self or context[1] not in compatible or not context[2]:
                raise PermissionError("Acesso indireto exige confirmação pelo executor.")

    def require_executor(self, tool_names: set[str]) -> None:
        context = _EFFECT_CONTEXT.get()
        if context is None or context[0] is not self or context[1] not in tool_names:
            raise PermissionError("Operação exige o executor existente.")

    def set_rule(self, resource: str, action: str, mode: str, *, expected_revision: str, expires: str | None = None) -> str:
        rule = {"resource": resource, "action": action, "mode": mode, "expires": expires, "origin": "user_admin"}
        self._validate(rule)
        with self._lock, self._operation_lock():
            rules, revision, valid = self.read()
            if not valid or revision != expected_revision:
                raise PermissionError("Configuração inválida ou alterada; recarregue antes de decidir.")
            rules = [r for r in rules if (r["resource"], r["action"]) != (resource, action)]
            rules.append(rule)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".permissions-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump({"schema": 1, "rules": rules}, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return self.read()[1]

    def check_tool(self, tool: object, arguments: dict) -> PermissionDecision:
        name = tool.name
        decision = self.decision("memory.delete" if name == "memory_forget" else f"tool:{name}", default="ask" if name == "memory_forget" else "allow")
        if name.startswith(("app_catalog_", "organization_")):
            specific = tool.permission_decision(arguments)
            decision = PermissionDecision("block" if "block" in {decision.mode, specific.mode} else "ask" if "ask" in {decision.mode, specific.mode} else "allow", specific.revision)
        catalog = getattr(self, "application_control", None)
        if catalog is not None and name in {"desktop_open_application", "desktop_focus_window", "desktop_close_window", "desktop_move_pointer_to_window"}:
            raise PermissionError("Rota desktop sem vínculo ao catálogo desabilitada; use a identidade autorizada de aplicativos.")
        if catalog is not None and name.startswith("local_application_"):
            if not any(e.legacy == arguments.get("application") for e in catalog.catalog.entries()):
                catalog.initialize_legacy()
            entry = next((e for e in catalog.catalog.entries() if e.legacy == arguments.get("application")), None)
            if entry is None:
                raise PermissionError("Identidade legada indisponível no catálogo.")
            specific = catalog.permission_decision(entry.id, name.removeprefix("local_application_"))
            decision = PermissionDecision("block" if "block" in {decision.mode, specific.mode} else "ask" if "ask" in {decision.mode, specific.mode} else "allow", specific.revision)
        for parameter in SCOPE_PARAMETERS:
            value = arguments.get(parameter)
            if isinstance(value, str):
                scoped = self.decision(scoped_resource(name, parameter, value))
                mode = "block" if "block" in {decision.mode, scoped.mode} else "ask" if "ask" in {decision.mode, scoped.mode} else "allow"
                decision = PermissionDecision(mode, scoped.revision)
        if decision.mode == "block":
            raise PermissionError("Operação bloqueada na Central de Permissões.")
        # These routes can indirectly open/read/write files without our guard.
        if self.has_path_restrictions() and indirect_tool(name):
            raise PermissionError("Rota indireta desabilitada enquanto há caminhos protegidos.")
        guard = getattr(tool, "filesystem", None) or getattr(getattr(tool, "vault", None), "filesystem", None)
        mode = decision.mode
        if guard is not None:
            action = "delete" if "delete" in name else "write" if name in {"create_file", "edit_file", "move_file", "obsidian_create_note", "obsidian_append_note"} else "read"
            paths = [arguments[k] for k in ("path", "source", "destination") if k in arguments]
            if not paths:
                paths = ["."]
            for path in paths:
                resolved = guard.resolve(path, for_write=action != "read")
                path_mode = self.path_decision(guard.base_directory / path, action)
                if name == "move_file" and path == arguments.get("source"):
                    source_mode = self.path_decision(guard.base_directory / path, "delete")
                    path_mode = "block" if "block" in {path_mode, source_mode} else "ask" if "ask" in {path_mode, source_mode} else "allow"
                if path_mode == "block":
                    raise PermissionError("Caminho bloqueado na Central de Permissões.")
                if path_mode == "ask":
                    mode = "ask"
                if name in {"list_files", "obsidian_list_notes", "obsidian_search_notes"}:
                    rules, _, _ = self.read()
                    if any(r["resource"].startswith("path:") and r["action"] == "read" and r["mode"] == "ask" and self._active(r) and Path(r["resource"][5:]).resolve().is_relative_to(resolved) for r in rules):
                        mode = "ask"
                if resolved.is_file() and resolved.stat().st_nlink > 1 and self.has_path_restrictions():
                    raise PermissionError("Hard link bloqueado no escopo protegido.")
        return PermissionDecision(mode, decision.revision)

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        mutex = "Local\\JARVIS.Permissions." + hashlib.sha256(str(self.path).encode()).hexdigest()[:24]
        lock = GoogleOperationLock(self.path.with_suffix(".lock"), mutex)
        try:
            lock.acquire()
        except GoogleOperationInProgressError:
            raise PermissionError("Outra operação local está em andamento; aguarde sua conclusão.") from None
        try:
            yield
        finally:
            lock.release()

    @contextmanager
    def effect(self, tool: object, arguments: dict, revision: str, *, per_operation: bool = False) -> Iterator[None]:
        if getattr(tool, 'permission_effect_per_operation', False) and not per_operation:
            # Batch coordination carries executor provenance, not a lock that
            # would prevent revocation between individual file effects.
            with self._lock, self._operation_lock():
                decision = self.check_tool(tool, arguments)
                if decision.revision != revision:
                    raise PermissionError('Permissões mudaram desde o preparo.')
            token = _EFFECT_CONTEXT.set((self, tool.name, decision.mode == 'ask'))
            try:
                yield
            finally:
                _EFFECT_CONTEXT.reset(token)
            return
        with self._lock, self._operation_lock():
            decision = self.check_tool(tool, arguments)
            if decision.revision != revision:
                raise PermissionError("Permissões mudaram desde o preparo; autorização invalidada.")
            token = _EFFECT_CONTEXT.set((self, tool.name, decision.mode == "ask"))
            try:
                yield
            finally:
                _EFFECT_CONTEXT.reset(token)

    @contextmanager
    def resource_effect(self, resource: str, *, revision: str | None = None, allow_ask: bool = False) -> Iterator[None]:
        with self._lock, self._operation_lock():
            decision = self.decision(resource, default="ask" if resource == "memory.delete" else "allow")
            if decision.mode == "block" or (decision.mode == "ask" and not allow_ask) or (revision is not None and decision.revision != revision):
                raise PermissionError("Permissão administrativa alterada ou bloqueada.")
            yield
