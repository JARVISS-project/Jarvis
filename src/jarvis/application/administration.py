"""Typed local administration. Not registered as Tools or callable by an LLM."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
import hashlib
import json
from queue import Queue, Full
from threading import RLock, Thread
from typing import Callable
from uuid import uuid4
from secrets import compare_digest, token_hex
from weakref import WeakSet

from jarvis.security.capabilities import TOOL_CAPABILITIES
from jarvis.security.risk import RiskLevel
from jarvis.security.permissions import scoped_resource, indirect_tool


class AdminMethod(str, Enum):
    RUNTIME_PREPARE_STOP = 'runtime.prepare_stop'
    RUNTIME_STOP = 'runtime.stop'
    RUNTIME_CANCEL_STOP = 'runtime.cancel_stop'
    DELEGATION_LIST = 'delegation.list'
    DELEGATION_CONFIGURE = 'delegation.configure'
    DELEGATION_PREPARE = 'delegation.prepare'
    DELEGATION_CANCEL = 'delegation.cancel'
    ROUTINES = 'routines.list'
    ROUTINE_SAVE = 'routines.save'
    ROUTINE_DELETE = 'routines.delete'
    ROUTINE_PREVIEW = 'routines.preview'
    HELP = 'help.list'
    HELP_PROGRESS = 'help.progress'
    HARDWARE = 'help.diagnose'
    PROFILE_PREPARE = 'help.profile_prepare'
    PROFILE_APPLY = 'help.profile_apply'
    PROFILE_ROLLBACK = 'help.profile_rollback'
    CONVERSATION_PREFERENCES = 'conversation.preferences'
    CONVERSATION_SAVE = 'conversation.save'
    SPEECH_PREVIEW = 'conversation.preview'
    SPEECH_STOP = 'conversation.stop'
    ACCOUNTS = 'accounts.list'
    ACCOUNT_GUIDE = 'accounts.calendar_guide'
    ACCOUNT_ADD = 'accounts.add'
    ACCOUNT_CHANGE = 'accounts.change'
    ACCOUNT_TEST = 'accounts.test'
    ACCOUNT_CONNECT = 'accounts.connect'
    ACCOUNT_CALENDARS = 'accounts.calendars'
    ACCOUNT_CALENDAR_SELECT = 'accounts.calendar_select'
    ACCOUNT_READ = 'accounts.read'
    ORGANIZATION_STATE = 'organization.state'
    ORGANIZATION_SELECT = 'organization.select'
    ORGANIZATION_RULES = 'organization.rules'
    ORGANIZATION_ANALYZE = 'organization.analyze'
    ORGANIZATION_GRANT = 'organization.grant'
    ORGANIZATION_PLAN = 'organization.plan'
    ORGANIZATION_INDEX = 'organization.index'
    ORGANIZATION_CANCEL = 'organization.cancel'
    ORGANIZATION_HISTORY = 'organization.history'
    APPLICATIONS = "apps.list"
    REFRESH_APPLICATIONS = "apps.refresh"
    APPLICATION_PERMISSION = "apps.permission"
    APPLICATION_PREFERENCES = "apps.preferences"
    PERMISSIONS = "permissions.list"
    SET_PERMISSION = "permissions.set"
    MEMORIES = "memory.list"
    SAVE_MEMORY = "memory.save"
    PREPARE_FORGET = "memory.prepare_forget"
    FORGET = "memory.forget"
    SESSION = "memory.session"


@dataclass(frozen=True)
class PermissionChange:
    resource: str
    action: str
    mode: str
    revision: str
    expires: str | None = None


@dataclass(frozen=True)
class MemoryDraft:
    content: str
    category: str
    context: bool
    external: bool
    retention_days: int | None
    id: str | None = None
    version: str | None = None


def memory_version(entry) -> str:
    return hashlib.sha256(json.dumps({"id": entry.id, "content": entry.content, "category": entry.category,
        "metadata": entry.metadata, "consent": entry.consent_given,
        "retention": entry.retention_days, "created": entry.created_at.isoformat()}, sort_keys=True).encode()).hexdigest()


class AdministrationService:
    def __init__(self, permissions, memory, registry, config, *, revoke: Callable[[], None] | None = None, audit=None, applications=None, execute_tool=None, organization=None, accounts=None) -> None:
        self.permissions, self.memory, self.registry, self.config = permissions, memory, registry, config
        permissions.protected_paths.update(p.resolve() for p in
            (config.settings_path, config.database_path, config.skill_store_path, config.audit_log_path))
        permissions.protected_paths.add(config.ambient_ui_endpoint_path.resolve())
        self.revoke = revoke or (lambda: None)
        self.audit = audit
        self.applications = applications
        self.organization = organization
        self.accounts = accounts
        self.conversation_preferences = None
        self.runtime_control = None
        if accounts is not None:
            permissions.protected_paths.add(accounts.directory.resolve())
        self._forget: dict[str, tuple[str, str, datetime, object, str]] = {}
        self._lock = RLock()
        self.capability_policy = None
        from jarvis.application.first_use import FirstUseService
        self.first_use = FirstUseService(self)

    def inventory(self) -> dict:
        rules, revision, valid = self.permissions.read()
        rows = []
        for tool in self.registry.list_tools():
            capability = TOOL_CAPABILITIES.get(tool.name)
            if capability is None:
                continue
            value = capability.value
            group = ("Arquivos/pastas" if value.startswith(("filesystem", "obsidian")) else
                     "Google e conexões" if value.startswith("google") else
                     "Navegador" if value in {"browser", "safe_browser"} else
                     "Microfone/captura de tela" if value == "vision" else
                     "Memória" if value == "local_briefing_read" else "Aplicativos")
            resource = f"tool:{tool.name}"
            mode = self.permissions.decision(resource).mode
            scope = "Tool específica; limites do backend preservados"
            if group == "Navegador":
                scope = "Política/egress existentes; domínios configurados: " + ", ".join(self.config.browser_allowed_domains)
            if group == "Arquivos/pastas":
                guard = getattr(tool, "filesystem", None) or getattr(getattr(tool, "vault", None), "filesystem", None)
                scope = str(guard.base_directory) if guard else scope
            elif group == "Google e conexões":
                scope = "Aliases autorizados: " + ", ".join(sorted(
                    self.config.google_calendar_write_calendars if value.endswith("calendar_write") else
                    self.config.google_calendar_authorized_calendars if "calendar" in value else self.config.google_drive_docs_files))
            action = "exclusão" if "delete" in tool.name or "close" in tool.name else "leitura" if tool.risk == RiskLevel.READ_ONLY else "alteração/efeito"
            effective = "bloqueado" if mode == "block" else ("perguntar" if mode == "ask" or tool.risk >= RiskLevel.EXTERNAL_EFFECT else "permitido no escopo")
            if group == "Arquivos/pastas" and guard is not None:
                path_action = "delete" if "delete" in tool.name else "write" if tool.risk != RiskLevel.READ_ONLY else "read"
                path_mode = self.permissions.path_decision(guard.base_directory, path_action)
                if path_mode == "block" or (path_action == "delete" and self.permissions.path_decision(guard.base_directory, "write") == "block"):
                    effective = "bloqueado: caminho configurado"
                elif path_mode == "ask" and mode != "block":
                    effective = "perguntar"
            if self.permissions.has_path_restrictions() and indirect_tool(tool.name):
                effective = "bloqueado: rota indireta de caminho protegido"
            if value == "vision" and self.permissions.decision("capture.screen").mode != "allow":
                effective = "bloqueado: captura de tela"
            rows.append(self._row(resource, "use", tool.description, group, action, scope, mode, effective, rules))
            selectors = []
            properties = tool.parameters.get("properties", {})
            for parameter in ("application", "destination"):
                for value in properties.get(parameter, {}).get("enum", []):
                    selectors.append((parameter, value, value))
            if hasattr(tool, "calendar_aliases"):
                selectors.extend(("calendar_id", value, alias) for alias, value in tool.calendar_aliases.items())
            for parameter, value, alias in selectors:
                key = scoped_resource(tool.name, parameter, value)
                scoped_mode = self.permissions.decision(key).mode
                combined = "block" if "block" in {mode, scoped_mode} else "ask" if "ask" in {mode, scoped_mode} or tool.risk >= RiskLevel.EXTERNAL_EFFECT else "allow"
                if effective.startswith("bloqueado"):
                    combined = effective
                rows.append(self._row(key, "use", f"{alias} · {tool.description}", group, action,
                    f"Somente {parameter}={alias}; identidade revalidada no backend", scoped_mode, combined, rules))
        for resource, title in (("memory.save", "Guardar novas memórias"), ("memory.edit", "Editar memórias"), ("memory.delete", "Esquecer memórias com confirmação"), ("memory.context", "Usar memória no contexto local"), ("memory.external", "Compartilhar memória com provedor externo")):
            mode = self.permissions.decision(resource, default="block" if resource.endswith("external") else "ask" if resource.endswith("delete") else "allow").mode
            operation = "compartilhamento externo" if resource.endswith("external") else "exclusão" if resource.endswith("delete") else "gravação" if resource.endswith(("save", "edit")) else "leitura"
            row = self._row(resource, "use", title, "Memória", operation, "Memórias explícitas; compartilhar exige consentimento individual; esquecer sempre exige confirmação", mode, mode, rules)
            row["modes"] = ["block", "ask", "allow"] if resource.endswith("delete") else ["block", "allow"]
            if self.memory.temporary and resource in {"memory.save", "memory.edit"}:
                row["effective"] = "bloqueado: sessão temporária"
            rows.append(row)
        if self.config.google_drive_read_enabled or self.config.google_docs_read_enabled or self.config.google_drive_docs_files:
            picker = self._row("tool:google_drive_picker_validate", "use", "Consentimento/seleção Google existente", "Google e conexões", "seleção administrativa", "Somente Picker oficial; permissão local não autoriza OAuth nem amplia READ", self.permissions.decision("tool:google_drive_picker_validate").mode, "consentimento separado obrigatório", rules)
            picker["modes"] = ["block", "allow"]
            rows.append(picker)
        microphone = self._row("microphone.capture", "use", "Microfone configurado", "Microfone/captura de tela", "captura", "Entrada de voz local configurada; não inicia captura", self.permissions.decision("microphone.capture").mode, "validado a cada frame; prontidão não consultada", rules)
        microphone["modes"] = ["block", "allow"]
        rows.append(microphone)
        if self.config.vision_enabled:
            capture = self._row("capture.screen", "use", "Captura de tela", "Microfone/captura de tela", "captura", "Backend de visão configurado; sem iniciar captura", self.permissions.decision("capture.screen").mode, "verificado antes de cada acesso", rules)
            capture["modes"] = ["block", "allow"]
            rows.append(capture)
        for root in self.permissions.roots:
            for action in ("read", "write", "delete"):
                resource = f"path:{root}"
                rows.append(self._row(resource, action, str(root), "Arquivos/pastas", action,
                    "Caminho resolvido e descendentes; rotas indiretas desabilitadas se restrito", self.permissions.path_decision(root, action), self.permissions.path_decision(root, action), rules))
        if self.applications is not None:
            for application in self.applications.rows()['rows']:
                for group, resource in application['grant_resources'].items():
                    mode = application['policies'][group if group != 'window' else 'maximize']
                    row = self._row(resource, 'use', f"{application['name']} · {group}", 'Aplicativos', group,
                        'Somente esta identidade/fingerprint; não autoriza conteúdo/arquivos', mode, mode, rules)
                    row['catalog_id'], row['catalog_fingerprint'] = application['id'], application['identity']
                    if not application['available']:
                        row['modes'] = []
                        row['effective'] = 'indisponível'
                    rows.append(row)
        if self.organization is not None and self.organization.selected:
            try:
                token, root, _ = self.organization.root()
                for tool_name, title in (('organization_execute', 'Organizar'), ('organization_undo', 'Desfazer')):
                    resource = self.organization.scope(tool_name, token)
                    mode = self.organization.decision(tool_name, token).mode
                    rows.append(self._row(resource, 'use', f'{title}: {root}', 'Arquivos/pastas', title,
                        'Raiz e regras selecionadas; mesma raiz/volume; sem excluir/sobrescrever/conteúdo externo', mode, mode, rules))
            except (OSError, ValueError, PermissionError):
                row = self._row('tool:organization_execute', 'use', 'Organização: seleção indisponível',
                    'Arquivos/pastas', 'Organizar', 'Selecione novamente; identidade/backend indisponível',
                    'block', 'indisponível', rules)
                row['modes'] = []
                rows.append(row)
        existing = {(r["resource"], r["action"]) for r in rows}
        for rule in rules:
            if rule["resource"].startswith("path:") and (rule["resource"], rule["action"]) not in existing:
                rows.append(self._row(rule["resource"], rule["action"], rule["resource"][5:], "Arquivos/pastas", rule["action"], "Descendente configurado", rule["mode"], self.permissions.path_decision(Path(rule["resource"][5:]), rule["action"]), rules))
        for resource, title in (("model.external", "Modelos externos"), ("delegation.execute", "Execução delegada externa")):
            mode = self.permissions.decision(resource, default='block').mode
            row = self._row(resource, "use", title, "Modelos externos e execução delegada", "envio textual explícito", "Exige chave API e confirmação por envio; Ollama continua padrão", mode, mode, rules)
            rows.append(row)
        return {"rows": rows, "revision": revision, "valid": valid, "temporary": self.memory.temporary,
                "explanation": "Revogar impede novas operações; não desfaz efeitos já realizados. Credencial não é autorização. Esta página não consulta contas nem backends."}

    @staticmethod
    def _row(resource, action, title, group, operation, scope, mode, effective, rules):
        matching = [r for r in rules if r["resource"] == resource and r["action"] == action]
        return {"resource": resource, "action": action, "title": title, "group": group, "operation": operation,
                "scope": scope, "mode": mode, "effective": effective, "modes": ["block", "ask", "allow"],
                "duration": ", ".join(r["expires"] or "até revogar" for r in matching) or "configuração existente",
                "origin": "usuário administrador" if matching else "configuração/política existente",
                "credential": "preservada; não consultada" if group == "Google e conexões" else "não aplicável",
                "readiness": "não sondada; prontidão não concede acesso"}

    def set_permission(self, change: PermissionChange) -> dict:
        inventory = self.inventory()
        rows = [r for r in inventory["rows"] if (r["resource"], r["action"]) == (change.resource, change.action)]
        # New descendant paths are accepted only inside a configured root.
        if not rows and change.resource.startswith("path:"):
            self.permissions._validate({"resource": change.resource, "action": change.action, "mode": change.mode, "expires": change.expires, "origin": "user_admin"})
        elif not rows or change.mode not in rows[0]["modes"]:
            raise PermissionError("Recurso/controle não disponível; não é possível ampliar a configuração existente.")
        if rows and rows[0].get('catalog_id'):
            entry = self.applications.catalog.get(rows[0]['catalog_id'])
            if entry.fingerprint != rows[0]['catalog_fingerprint']:
                raise PermissionError('Identidade mudou; atualize antes de autorizar.')
        self.permissions.set_rule(change.resource, change.action, change.mode,
            expected_revision=change.revision, expires=change.expires)
        self.revoke()
        self._audit("permission_change")
        return self.inventory()

    def list_memories(self, query: str = "") -> dict:
        if not isinstance(query, str) or len(query) > 2000:
            raise ValueError("Busca inválida.")
        entries = [e for e in self.memory.get_all() if query.casefold() in (e.content + " " + e.category).casefold()]
        rows = [{"id": e.id, "content": e.content, "category": e.category, "origin": e.provenance.value,
                 "created": e.created_at.isoformat(), "retention_days": e.retention_days,
                 "context": e.consent_given, "external": e.metadata.get("share_external") is True,
                 "version": memory_version(e)} for e in entries[:100]]
        return {"rows": rows, "total": len(entries), "temporary": self.memory.temporary,
                "limit": "Mostrando até 100 registros; refine a busca."}

    def save_memory(self, draft: MemoryDraft) -> dict:
        if not isinstance(draft.category, str) or not 0 < len(draft.category.strip()) <= 64 or type(draft.context) is not bool or type(draft.external) is not bool:
            raise ValueError("Categoria/consentimento inválido.")
        if draft.retention_days is not None and (type(draft.retention_days) is not int or not 1 <= draft.retention_days <= 36500):
            raise ValueError("Validade inválida.")
        if draft.external and self.permissions.decision("memory.external", default="block").mode != "allow":
            raise PermissionError("Compartilhamento externo bloqueado na Central de Permissões.")
        metadata = {"share_external": draft.external}
        if draft.id is None:
            if draft.version is not None:
                raise ValueError("Versão não aplicável à inclusão.")
            self.memory.add(draft.content, draft.category, consent_given=draft.context,
                retention_days=draft.retention_days, metadata=metadata)
        else:
            target = next((e for e in self.memory.get_all() if e.id == draft.id), None)
            if target is None or memory_version(target) != draft.version:
                raise PermissionError("Memória alterada ou esquecida; recarregue antes de editar.")
            metadata = {**target.metadata, **metadata}
            self.memory.update(draft.id, content=draft.content, category=draft.category,
                consent_given=draft.context, retention_days=draft.retention_days, metadata=metadata, expected_version=draft.version)
        self._audit("memory_write")
        return self.list_memories()

    def prepare_forget(self, memory_id: str, *, actor=None) -> dict:
        if self.permissions.decision("memory.delete", default="ask").mode == "block":
            raise PermissionError("Esquecimento bloqueado.")
        target = next((e for e in self.memory.get_all() if e.id == memory_id), None)
        if target is None:
            raise ValueError("Memória não encontrada.")
        token = uuid4().hex
        now = datetime.now(timezone.utc)
        self._forget = {k: v for k, v in self._forget.items() if v[2] > now}
        if len(self._forget) >= 64:
            raise PermissionError("Muitas confirmações pendentes.")
        self._forget[token] = (memory_id, memory_version(target), now + timedelta(seconds=60), actor, self.permissions.read()[1])
        return {"token": token, "id": memory_id,
                "preview": "Esquecer este registro? Ele sairá das consultas e contextos gerenciados. Backups e cópias externas já enviadas podem permanecer.",
                "content": target.content}

    def forget(self, token: str, *, actor=None) -> dict:
        prepared = self._forget.pop(token, None)
        if prepared is None or prepared[2] <= datetime.now(timezone.utc) or prepared[3] is not actor or prepared[4] != self.permissions.read()[1]:
            raise PermissionError("Confirmação expirada ou já utilizada.")
        target = next((e for e in self.memory.get_all() if e.id == prepared[0]), None)
        if target is None or memory_version(target) != prepared[1]:
            raise PermissionError("Memória mudou antes da confirmação.")
        with self.permissions.resource_effect("memory.delete", revision=prepared[4], allow_ask=True):
            current = next((e for e in self.memory.get_all() if e.id == target.id), None)
            if current is None or memory_version(current) != prepared[1]:
                raise PermissionError("Memória mudou antes de esquecer.")
            if not self.memory.delete(target.id) or any(e.id == target.id for e in self.memory.get_all()):
                raise RuntimeError("Esquecimento não verificado.")
        self._audit("memory_forget")
        return self.list_memories()

    def _audit(self, action: str) -> None:
        if self.audit is not None:
            self.audit.log(action, "local-administration", True, "verified")

    def account_policies(self, data):
        if 'rows' not in data:
            return data
        tools = {'calendar_read': ['google_calendar_list_events'],
            'calendar_write': ['google_calendar_create_event', 'google_calendar_update_event'],
            'drive_docs_read': ['google_drive_find_files', 'google_drive_select_file', 'google_drive_metadata', 'google_docs_read']}
        for row in data['rows']:
            policies = []
            for tool_name in tools.get(row['operation'], []):
                mode = self.permissions.decision('tool:' + tool_name).mode
                scopes = [self.permissions.decision(scoped_resource(tool_name, 'calendar_id', calendar)).mode
                          for calendar in row['resources'].values()] if 'calendar' in row['operation'] else []
                effective = 'block' if 'block' in [mode, *scopes] else 'ask' if 'ask' in [mode, *scopes] else mode
                enabled = (self.config.google_calendar_write_enabled if row['operation'] == 'calendar_write' else
                           self.config.google_calendar_enabled if row['operation'] == 'calendar_read' else
                           self.config.google_docs_read_enabled if tool_name == 'google_docs_read' else self.config.google_drive_read_enabled)
                policies.append({'tool': tool_name, 'policy': effective, 'enabled': enabled,
                                 'confirmation': row['operation'] == 'calendar_write'})
            row['policies'] = policies
        return data

    def dispatch(self, method: str, arguments: dict, *, actor=None) -> dict:
        with self._lock:
            method = AdminMethod(method)
            schemas = {AdminMethod.PERMISSIONS: set(), AdminMethod.SET_PERMISSION: {"resource", "action", "mode", "revision", "expires"},
                AdminMethod.RUNTIME_PREPARE_STOP: set(), AdminMethod.RUNTIME_STOP: {'token'},
                AdminMethod.RUNTIME_CANCEL_STOP: {'token'},
                AdminMethod.DELEGATION_LIST: set(),
                AdminMethod.DELEGATION_CONFIGURE: {'provider', 'model', 'input_limit', 'output_limit', 'timeout', 'key'},
                AdminMethod.DELEGATION_PREPARE: {'provider', 'task', 'context'}, AdminMethod.DELEGATION_CANCEL: set(),
                AdminMethod.ROUTINES: set(), AdminMethod.ROUTINE_SAVE: {'definition', 'revision'},
                AdminMethod.ROUTINE_DELETE: {'id', 'revision'}, AdminMethod.ROUTINE_PREVIEW: {'id'},
                AdminMethod.HELP: set(), AdminMethod.HELP_PROGRESS: {'action'}, AdminMethod.HARDWARE: set(),
                AdminMethod.PROFILE_PREPARE: {'profile'}, AdminMethod.PROFILE_APPLY: {'token'}, AdminMethod.PROFILE_ROLLBACK: set(),
                AdminMethod.CONVERSATION_PREFERENCES: set(),
                AdminMethod.CONVERSATION_SAVE: {'user_display_name', 'tone', 'detail', 'opinions', 'spoken', 'voice', 'pace'},
                AdminMethod.SPEECH_PREVIEW: set(), AdminMethod.SPEECH_STOP: set(),
                AdminMethod.ACCOUNTS: set(), AdminMethod.ACCOUNT_GUIDE: set(), AdminMethod.ACCOUNT_ADD: {'operation', 'label', 'revision'},
                AdminMethod.ACCOUNT_CHANGE: {'connection', 'revision', 'action', 'value'},
                AdminMethod.ACCOUNT_TEST: {'connection', 'revision'},
                AdminMethod.ACCOUNT_CONNECT: {'connection', 'revision', 'client_config'},
                AdminMethod.ACCOUNT_CALENDARS: {'connection', 'revision'},
                AdminMethod.ACCOUNT_CALENDAR_SELECT: {'read_connection', 'write_connection', 'revision', 'calendar', 'alias'},
                AdminMethod.ACCOUNT_READ: {'enabled'},
                AdminMethod.ORGANIZATION_STATE: set(), AdminMethod.ORGANIZATION_SELECT: {'path'},
                AdminMethod.ORGANIZATION_RULES: {'root', 'rules'}, AdminMethod.ORGANIZATION_ANALYZE: {'root'},
                AdminMethod.ORGANIZATION_GRANT: {'root', 'action', 'mode', 'revision'},
                AdminMethod.ORGANIZATION_PLAN: {'plan'}, AdminMethod.ORGANIZATION_INDEX: {'root', 'query'},
                AdminMethod.ORGANIZATION_CANCEL: set(),
                AdminMethod.ORGANIZATION_HISTORY: {'root'},
                AdminMethod.APPLICATIONS: set(), AdminMethod.REFRESH_APPLICATIONS: set(),
                AdminMethod.APPLICATION_PERMISSION: {'application', 'fingerprint', 'group', 'mode', 'revision', 'remove'},
                AdminMethod.APPLICATION_PREFERENCES: {'application', 'fingerprint', 'preference', 'aliases'},
                AdminMethod.MEMORIES: {"query"}, AdminMethod.SAVE_MEMORY: {"content", "category", "context", "external", "retention_days", "id", "version"},
                AdminMethod.PREPARE_FORGET: {"id"}, AdminMethod.FORGET: {"token"}, AdminMethod.SESSION: {"temporary"}}
            if not isinstance(arguments, dict) or set(arguments) != schemas[method]:
                raise ValueError("Argumentos administrativos inválidos.")
            if method.value.startswith('runtime.'):
                if self.runtime_control is None:
                    raise PermissionError('Encerramento indisponível neste runtime.')
                if method is AdminMethod.RUNTIME_PREPARE_STOP:
                    return self.runtime_control.prepare(actor)
                result = self.runtime_control.finish(arguments['token'], actor,
                    cancel=method is AdminMethod.RUNTIME_CANCEL_STOP)
                if result.get('requested'):
                    self._audit('runtime_stop_requested')
                return result
            if method.value.startswith('delegation.'):
                service = getattr(self, 'delegation', None)
                if service is None:
                    raise PermissionError('Delegação indisponível.')
                if method is AdminMethod.DELEGATION_LIST:
                    return service.inventory()
                if method is AdminMethod.DELEGATION_CONFIGURE:
                    return service.configure(**arguments)
                if method is AdminMethod.DELEGATION_PREPARE:
                    return service.prepare(**arguments)
                service.invalidate()
                return {'state': 'cancelled'}
            if method.value.startswith('routines.'):
                service = getattr(self, 'routines', None)
                if service is None:
                    raise PermissionError('Rotinas indisponíveis neste runtime.')
                if method is AdminMethod.ROUTINES:
                    return service.inventory()
                if method is AdminMethod.ROUTINE_SAVE:
                    return service.save(arguments['definition'], arguments['revision'])
                if method is AdminMethod.ROUTINE_DELETE:
                    return service.delete(arguments['id'], arguments['revision'])
                return service.preview(arguments['id'])
            if method.value.startswith('help.'):
                if method is AdminMethod.HELP:
                    return self.first_use.inventory()
                if method is AdminMethod.HELP_PROGRESS:
                    return self.first_use.update(arguments['action'])
                if method is AdminMethod.HARDWARE:
                    return self.first_use.diagnose()
                if method is AdminMethod.PROFILE_PREPARE:
                    return self.first_use.prepare_profile(arguments['profile'], actor)
                if method is AdminMethod.PROFILE_APPLY:
                    result = self.first_use.apply_profile(arguments['token'], actor)
                else:
                    result = self.first_use.rollback_profile(actor)
                self._audit('hardware_profile')
                return result
            if method.value.startswith('conversation.'):
                service = self.conversation_preferences
                if service is None:
                    raise PermissionError('Preferências indisponíveis neste runtime.')
                if method is AdminMethod.CONVERSATION_SAVE:
                    data = service.save(**arguments)
                    self._audit('conversation_preferences')
                    return data
                if method is AdminMethod.SPEECH_PREVIEW:
                    return service.preview()
                if method is AdminMethod.SPEECH_STOP:
                    return service.stop()
                return service.inventory()
            if method.value.startswith('accounts.'):
                if self.accounts is None:
                    raise PermissionError('Central de contas indisponível neste runtime.')
                if method is AdminMethod.ACCOUNTS:
                    return self.account_policies(self.accounts.inventory())
                if method in {AdminMethod.ACCOUNT_CALENDARS, AdminMethod.ACCOUNT_CALENDAR_SELECT, AdminMethod.ACCOUNT_GUIDE}:
                    if not self.config.google_calendar_enabled:
                        raise PermissionError('Leitura Calendar desabilitada; revise Permissões.')
                    if self.permissions.decision('tool:google_calendar_list_events').mode == 'block':
                        raise PermissionError('Consulta Calendar bloqueada pela política.')
                if method is AdminMethod.ACCOUNT_GUIDE:
                    self.revoke()
                    result = self.accounts.guided_calendar(calendar_allowed=lambda calendar:
                        self.permissions.decision(scoped_resource('google_calendar_list_events', 'calendar_id', calendar)).mode != 'block')
                    # Scoped blocks must apply to administrative discovery too.
                    result['calendars'] = [item for item in result['calendars'] if
                        self.permissions.decision(scoped_resource('google_calendar_list_events', 'calendar_id', item['id'])).mode != 'block']
                    for item in result['calendars']:
                        if any('block' in {self.permissions.decision('tool:' + name).mode,
                            self.permissions.decision(scoped_resource(name, 'calendar_id', item['id'])).mode}
                            for name in ('google_calendar_create_event', 'google_calendar_update_event')):
                            item['write_candidate'] = False
                    self.revoke()
                    return self.account_policies(result)
                if method is AdminMethod.ACCOUNT_CALENDAR_SELECT:
                    if self.permissions.decision(scoped_resource('google_calendar_list_events', 'calendar_id', arguments['calendar'])).mode == 'block':
                        raise PermissionError('Agenda bloqueada pela política.')
                self.revoke()
                functions = {AdminMethod.ACCOUNT_ADD: self.accounts.add,
                    AdminMethod.ACCOUNT_CHANGE: self.accounts.change,
                    AdminMethod.ACCOUNT_TEST: self.accounts.test,
                    AdminMethod.ACCOUNT_CONNECT: self.accounts.connect,
                    AdminMethod.ACCOUNT_CALENDARS: self.accounts.calendars,
                    AdminMethod.ACCOUNT_CALENDAR_SELECT: self.accounts.select_calendar}
                if method is AdminMethod.ACCOUNT_READ:
                    if type(arguments['enabled']) is not bool:
                        raise ValueError('Flag READ inválida.')
                    if arguments['enabled']:
                        key, row = self.accounts.default('calendar_read')
                        if not row['resources'] or self.accounts._verified.get(key, ('',))[0] != 'conectado':
                            raise PermissionError('Selecione e teste a conta/calendário READ primeiro.')
                    self.config.save_calendar_settings(enabled=arguments['enabled'])
                    self.accounts.invalidate('calendar_read')
                    result = self.accounts.inventory()
                else:
                    extra = {'calendar_allowed': lambda calendar:
                        self.permissions.decision(scoped_resource('google_calendar_list_events', 'calendar_id', calendar)).mode != 'block'} if method in {
                            AdminMethod.ACCOUNT_CALENDARS, AdminMethod.ACCOUNT_CALENDAR_SELECT} else {}
                    result = functions[method](**arguments, **extra)
                self.revoke()
                self._audit('account_administration')
                return self.account_policies(result)
            if method.value.startswith('organization.'):
                if self.organization is None:
                    raise PermissionError('Organização indisponível.')
                if method not in {AdminMethod.ORGANIZATION_STATE, AdminMethod.ORGANIZATION_CANCEL} and not self.config.filesystem_read_enabled:
                    raise PermissionError('Capability de leitura de metadados desabilitada.')
                if method is AdminMethod.ORGANIZATION_STATE:
                    return self.organization.describe()
                if method is AdminMethod.ORGANIZATION_SELECT:
                    result = self.organization.select(arguments['path'])
                elif method is AdminMethod.ORGANIZATION_RULES:
                    result = self.organization.rules(arguments['root'], arguments['rules'])
                elif method is AdminMethod.ORGANIZATION_ANALYZE:
                    return self.organization.analyze(arguments['root'])
                elif method is AdminMethod.ORGANIZATION_PLAN:
                    plan = self.organization.view(arguments['plan'])
                    self.organization.root(plan['root'])
                    self.organization.reconcile(plan)
                    return plan
                elif method is AdminMethod.ORGANIZATION_INDEX:
                    return self.organization.index(arguments['root'], arguments['query'])
                elif method is AdminMethod.ORGANIZATION_HISTORY:
                    return self.organization.history(arguments['root'])
                elif method is AdminMethod.ORGANIZATION_CANCEL:
                    return self.organization.cancel()
                else:
                    result = self.organization.grant(arguments['root'], arguments['action'], arguments['mode'], arguments['revision'])
                self.revoke()
                self._audit('organization_administration')
                return result
            if method in {AdminMethod.APPLICATIONS, AdminMethod.REFRESH_APPLICATIONS, AdminMethod.APPLICATION_PERMISSION, AdminMethod.APPLICATION_PREFERENCES}:
                if self.applications is None:
                    raise PermissionError('Catálogo Windows indisponível.')
                if method is AdminMethod.APPLICATIONS:
                    return self.applications.rows()
                if method is AdminMethod.REFRESH_APPLICATIONS:
                    result = self.applications.refresh()
                elif method is AdminMethod.APPLICATION_PERMISSION:
                    if type(arguments['remove']) is not bool:
                        raise ValueError('Revogação inválida.')
                    result = self.applications.change_permission(**arguments)
                    self._audit('application_permission_change')
                else:
                    result = self.applications.set_preferences(**arguments)
                    self._audit('application_preferences_change')
                self.revoke()
                return result
            if method is AdminMethod.PERMISSIONS:
                return self.inventory()
            if method is AdminMethod.SET_PERMISSION:
                return self.set_permission(PermissionChange(**arguments))
            if method is AdminMethod.MEMORIES:
                return self.list_memories(arguments["query"])
            if method is AdminMethod.SAVE_MEMORY:
                return self.save_memory(MemoryDraft(**arguments))
            if method is AdminMethod.PREPARE_FORGET:
                return self.prepare_forget(arguments["id"], actor=actor)
            if method is AdminMethod.FORGET:
                return self.forget(arguments["token"], actor=actor)
            if type(arguments["temporary"]) is not bool:
                raise ValueError("Sessão inválida.")
            self.memory.set_temporary(arguments["temporary"])
            return self.list_memories()


def valid_admin_command(payload: object) -> bool:
    keys = {"command", "generation", "interaction", "method", "arguments"}
    if not isinstance(payload, dict) or set(payload) not in (keys, keys | {"authorization"}) or payload.get("command") != "admin_request":
        return False
    if "authorization" in payload and (not isinstance(payload["authorization"], str) or len(payload["authorization"]) != 64 or any(c not in "0123456789abcdef" for c in payload["authorization"])):
        return False
    if not all(isinstance(payload.get(k), str) and len(payload[k]) == 32 and all(c in "0123456789abcdef" for c in payload[k]) for k in ("generation", "interaction")):
        return False
    return isinstance(payload.get("method"), str) and payload["method"] in {m.value for m in AdminMethod} and isinstance(payload.get("arguments"), dict)


class AdministrationController:
    """Bounded local worker; replies contain private content only for requester."""
    def __init__(self, service: AdministrationService, generation: str) -> None:
        self.service, self.generation = service, generation
        self.authorization = token_hex(32)
        self._queue = Queue(maxsize=8)
        self._seen: set[str] = set()
        self._closed = False
        self._disconnected = WeakSet()
        self._lock = RLock()
        self._thread = Thread(target=self._run, name="jarvis-ui-admin", daemon=False)
        self._thread.start()

    def handle(self, payload: dict, reply: Callable, *, owner=None) -> None:
        event = {"type": "admin", "generation": self.generation, "interaction": payload.get("interaction", "")}
        with self._lock:
            if self._closed or not valid_admin_command(payload) or not compare_digest(payload.get("authorization", ""), self.authorization) or payload["generation"] != self.generation or payload["interaction"] in self._seen or len(self._seen) >= 4096:
                reply({**event, "status": "error", "error": "Administração inválida, antiga ou duplicada."})
                return
            try:
                encoded = json.dumps(payload, ensure_ascii=False)
                if len(encoded.encode("utf-8")) > 16384:
                    raise ValueError
                payload = json.loads(encoded)
            except (TypeError, ValueError, RecursionError):
                reply({**event, "status": "error", "error": "Pedido administrativo inválido ou acima do limite."})
                return
            try:
                self._queue.put_nowait((payload, reply, event, owner))
            except Full:
                reply({**event, "status": "error", "error": "Administração ocupada; solicitação não aceita."})
                return
            self._seen.add(payload["interaction"])

    def _run(self) -> None:
        while (item := self._queue.get()) is not None:
            payload, reply, event, owner = item
            if self._closed or (owner is not None and owner in self._disconnected):
                continue
            try:
                data = self.service.dispatch(payload["method"], payload["arguments"], actor=owner)
                reply({**event, "status": "done", "data": data})
            except Exception as error:
                # No exception/payload/private data in logs or UI errors.
                safe = "Operação bloqueada; recarregue a configuração, escopo e consentimento." if isinstance(error, PermissionError) else "Dados inválidos ou serviço indisponível; nenhuma operação confirmada."
                reply({**event, "status": "error", "error": safe})

    def disconnect(self, owner: object) -> None:
        with self._lock:
            self._disconnected.add(owner)
        with self.service._lock:
            self.service._forget = {k: v for k, v in self.service._forget.items() if v[3] is not owner}
            if self.service.runtime_control is not None:
                self.service.runtime_control.disconnect(owner)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        if getattr(self.service, 'accounts', None) is not None:
            self.service.accounts.cancel_connection()
        self._queue.put(None)
        self._thread.join()
        with self.service._lock:
            self.service._forget.clear()
            if self.service.runtime_control is not None:
                self.service.runtime_control.close()
