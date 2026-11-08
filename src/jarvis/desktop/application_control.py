"""Prepared application operations: catalog evidence, policy, live target and verification."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from threading import RLock
import time
from uuid import uuid4

from jarvis.desktop.application_catalog import ApplicationCatalog, DiscoveredApplication
from jarvis.desktop.application_windows import ApplicationWindow, preferred_rectangle
from jarvis.desktop.catalog import _normalize
from jarvis.desktop.control import DesktopKillSwitch
from jarvis.security.permissions import PermissionDecision, scoped_resource

TOOLS = {'open': 'app_catalog_open', 'status': 'app_catalog_status', 'window': 'app_catalog_window', 'close': 'app_catalog_close'}
ACTIONS = {'open', 'status', 'focus', 'minimize', 'maximize', 'restore', 'place', 'close'}


class ApplicationSettings:
    """Local preferences and restrictive legacy identity snapshots; not operational grants."""
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()

    def read(self) -> dict:
        try:
            from jarvis.security.permissions import PermissionStore
            value = json.loads(self.path.read_text(encoding='utf-8'), object_pairs_hook=PermissionStore._pairs)
        except FileNotFoundError:
            return {'schema': 1, 'preferences': {}, 'aliases': {}, 'legacy_bindings': {}}
        if not isinstance(value, dict) or set(value) != {'schema', 'preferences', 'aliases', 'legacy_bindings'} or type(value['schema']) is not int or value['schema'] != 1:
            raise ValueError('Preferências inválidas; nenhuma autorização ampliada.')
        if any(not isinstance(value[key], dict) or len(value[key]) > 5000 for key in ('preferences', 'aliases', 'legacy_bindings')):
            raise ValueError('Preferências inválidas.')
        for preference in value['preferences'].values():
            self.validate_preference(preference)
        if any(not isinstance(k, str) or not isinstance(v, str) for key in ('aliases', 'legacy_bindings') for k, v in value[key].items()):
            raise ValueError('Alias/identidade legada inválida.')
        import re
        if any(not 0 < len(k) <= 100 or not re.fullmatch(r'app-[a-f0-9]{32}', v) for k, v in value['aliases'].items()):
            raise ValueError('Alias/identidade inválida.')
        if any(k not in {'spotify', 'obsidian'} or not re.fullmatch(r'[a-f0-9]{64}', v) for k, v in value['legacy_bindings'].items()):
            raise ValueError('Identidade legada inválida.')
        return value

    @staticmethod
    def validate_preference(preference):
        if not isinstance(preference, dict) or set(preference) != {'monitor', 'x', 'y', 'width', 'height'} or not isinstance(preference['monitor'], str) or len(preference['monitor']) > 100:
            raise ValueError('Preferência inválida.')
        for key in ('x', 'y', 'width', 'height'):
            lower, upper = (0, 100) if key in {'x', 'y'} else (10, 100)
            if type(preference[key]) is not int or not lower <= preference[key] <= upper:
                raise ValueError('Posição/tamanho devem ser percentuais válidos.')

    def update(self, function):
        from jarvis.remote.auth import _atomic_write_json
        with self._lock:
            value = self.read()
            function(value)
            _atomic_write_json(self.path, value)
            self.read()


@dataclass(frozen=True)
class PreparedCatalogAction:
    entry: DiscoveredApplication
    action: str
    instance: ApplicationWindow | None
    generation: str
    preference: dict | None
    settings_digest: str
    permission_revision: str

    def digest(self):
        return hashlib.sha256(f'{self.entry.grant_key}|{self.action}|{self.instance.identity() if self.instance else "none"}|{self.generation}|{self.settings_digest}|{self.permission_revision}'.encode()).hexdigest()


class ApplicationChoiceRequired(ValueError):
    pass


class ApplicationControlService:
    def __init__(self, catalog: ApplicationCatalog, backend, permissions, settings: ApplicationSettings,
                 kill_switch: DesktopKillSwitch | None = None, *, enabled=True,
                 verification_timeout=3.0, clock=time.monotonic, sleep=time.sleep):
        self.catalog, self.backend, self.permissions, self.settings = catalog, backend, permissions, settings
        self.kill_switch = kill_switch or DesktopKillSwitch()
        self.enabled = enabled
        self.verification_timeout, self.clock, self.sleep = verification_timeout, clock, sleep
        self.generation = uuid4().hex
        self._references: dict[str, tuple[str, str, ApplicationWindow]] = {}
        self._lock = RLock()
        permissions.protected_paths.add(settings.path.resolve())

    def refresh(self):
        with self._lock:
            self.catalog.refresh()
            self.generation = uuid4().hex
            self._references.clear()
            if self.enabled:
                def bind(value):
                    for entry in self.catalog.entries():
                        if entry.legacy and entry.available:
                            value['legacy_bindings'].setdefault(entry.legacy, entry.fingerprint)
                self.settings.update(bind)
            return self.rows()

    def initialize_legacy(self):
        self.catalog.seed(self.catalog.discovery.legacy_entries())
        if self.enabled and self.catalog.entries():
            def bind(value):
                for entry in self.catalog.entries():
                    if entry.legacy and entry.available:
                        value['legacy_bindings'].setdefault(entry.legacy, entry.fingerprint)
            self.settings.update(bind)

    @staticmethod
    def group(action):
        return action if action in {'open', 'status', 'close'} else 'window'

    def mode(self, entry, action) -> PermissionDecision:
        group = self.group(action)
        tool = TOOLS[group]
        global_decision = self.permissions.decision('tool:' + tool)
        scoped = self.permissions.decision(scoped_resource(tool, 'application', entry.grant_key), default='block')
        rules, _, valid = self.permissions.read()
        explicit = any(r['resource'] == scoped_resource(tool, 'application', entry.grant_key) for r in rules)
        legacy_tool = None
        if entry.legacy and action in {'open', 'status', 'focus', 'close'}:
            legacy_tool = f'local_application_{action}'
            legacy_global = self.permissions.decision('tool:' + legacy_tool)
            legacy_scoped = self.permissions.decision(scoped_resource(legacy_tool, 'application', entry.legacy))
            inherited = self.settings.read()['legacy_bindings'].get(entry.legacy) == entry.fingerprint
            if not explicit and inherited:
                scoped = PermissionDecision('ask' if 'ask' in {legacy_global.mode, legacy_scoped.mode} else 'allow', scoped.revision)
            if 'block' in {legacy_global.mode, legacy_scoped.mode}:
                scoped = PermissionDecision('block', scoped.revision)
        mode = 'block' if not valid or not self.enabled or 'block' in {global_decision.mode, scoped.mode} else 'ask' if 'ask' in {global_decision.mode, scoped.mode} else 'allow'
        # A second discovery source is not an escape from an explicit deny.
        related = [candidate for candidate in self.catalog.entries() if candidate.target and candidate.target.casefold() == entry.target.casefold()]
        resources = {scoped_resource(tool, 'application', candidate.grant_key) for candidate in related}
        if any(rule['resource'] in resources and rule['mode'] == 'block' and self.permissions._active(rule) for rule in rules):
            mode = 'block'
        if self.permissions.has_path_restrictions():
            mode = 'block'
        return PermissionDecision(mode, scoped.revision)

    def permission_decision(self, application, action):
        entry = self.catalog.get(application)
        return self.mode(entry, action)

    def rows(self):
        entries = []
        for entry in self.catalog.entries():
            entries.append({'id': entry.id, 'name': entry.name, 'identity': entry.fingerprint,
                'source': entry.source, 'kind': entry.kind, 'available': entry.available and self.enabled,
                'reason': entry.reason or ('' if self.enabled else 'Capability local desabilitada.'),
                'operations': sorted(ACTIONS) if entry.available and self.enabled else [],
                'policies': {action: self.mode(entry, action).mode for action in ACTIONS},
                'grant_resources': {group: scoped_resource(tool, 'application', entry.grant_key) for group, tool in TOOLS.items()},
                'preference': self.settings.read()['preferences'].get(entry.grant_key),
                'aliases': [alias for alias, target in self.settings.read()['aliases'].items() if target == entry.id]})
        try:
            monitors = [{'id': m.id, 'work': list(m.work), 'primary': m.primary} for m in self.backend.monitors()]
            diagnostics = list(self.catalog.diagnostics)
        except (OSError, RuntimeError):
            monitors, diagnostics = [], [*self.catalog.diagnostics, 'Monitores indisponíveis; preferências não serão aplicadas sem área útil.']
        return {'rows': entries, 'revision': self.permissions.read()[1], 'diagnostics': diagnostics, 'monitors': monitors,
            'explanation': 'Descoberta não autoriza; permitir abrir não dá acesso a arquivos/conteúdo. Maximizar janela não é tela cheia interna.'}

    def change_permission(self, application, fingerprint, group, mode, revision, *, remove=False):
        entry = self.catalog.get(application)
        if entry.fingerprint != fingerprint or group not in TOOLS or mode not in {'block', 'ask', 'allow'} or not self.enabled:
            raise PermissionError('Alvo/permissão mudou ou operação indisponível.')
        resource = scoped_resource(TOOLS[group], 'application', entry.grant_key)
        # Removing a grant leaves an explicit deny, including imported legacy scopes.
        self.permissions.set_rule(resource, 'use', 'block' if remove else mode, expected_revision=revision)
        return self.rows()

    def set_preferences(self, application, fingerprint, preference, aliases):
        entry = self.catalog.get(application)
        if entry.fingerprint != fingerprint:
            raise PermissionError('Identidade mudou.')
        ApplicationSettings.validate_preference(preference)
        if not isinstance(aliases, list) or len(aliases) > 20 or any(not isinstance(alias, str) or not 0 < len(alias) <= 100 for alias in aliases):
            raise ValueError('Aliases inválidos.')
        normalized = [_normalize(alias) for alias in aliases]
        if len(set(normalized)) != len(normalized) or any(not alias for alias in normalized):
            raise ValueError('Colisão de aliases.')
        def save(value):
            for alias in normalized:
                matches = self.catalog.match(alias, value['aliases'])
                if any(candidate.id != entry.id for candidate in matches) or value['aliases'].get(alias, entry.id) != entry.id:
                    raise ValueError('Alias colide com outro aplicativo.')
            value['aliases'] = {alias: target for alias, target in value['aliases'].items() if target != entry.id}
            value['aliases'].update({alias: entry.id for alias in normalized})
            value['preferences'][entry.grant_key] = dict(preference)
        self.settings.update(save)
        return self.rows()

    def windows(self, application):
        entry = self.catalog.get(application)
        if self.mode(entry, 'status').mode == 'block':
            raise PermissionError('Consulta bloqueada.')
        windows = self.backend.windows(entry)
        with self._lock:
            choices = []
            for window in windows:
                token = hashlib.sha256(f'{self.generation}|{entry.grant_key}|{window.identity()}'.encode()).hexdigest()[:32]
                self._references[token] = (entry.id, entry.fingerprint, window)
                choices.append({'reference': token, 'pid': window.pid, 'hwnd': window.hwnd, 'title': window.title})
            if len(self._references) > 1024:
                self._references.clear()
                raise RuntimeError('Referências excedidas; consulte novamente.')
        return {'open': bool(windows), 'windows': choices}

    def prepare(self, application, action, reference=None):
        if action not in ACTIONS:
            raise ValueError('Ação inválida.')
        entry = self.catalog.get(application)
        decision = self.mode(entry, action)
        if decision.mode == 'block':
            raise PermissionError('Operação de aplicativo bloqueada; autorize o escopo na Central.')
        windows = self.backend.windows(entry)
        instance = None
        if reference is not None:
            selected = self._references.get(reference)
            if selected is None or selected[:2] != (entry.id, entry.fingerprint) or selected[2] not in windows:
                raise PermissionError('Referência antiga/substituída; consulte as janelas novamente.')
            instance = selected[2]
        elif len(windows) > 1 and action != 'status':
            raise ApplicationChoiceRequired('Múltiplas janelas: consulte e selecione uma referência explícita.')
        elif len(windows) == 1:
            instance = windows[0]
        if action not in {'open', 'status'} and instance is None:
            raise ValueError('Aplicativo sem janela compatível aberta.')
        value = self.settings.read()
        preference = value['preferences'].get(entry.grant_key) if action == 'place' else None
        if action == 'place' and preference is None:
            raise ValueError('Configure posição/tamanho antes de aplicar.')
        settings_digest = hashlib.sha256(json.dumps(preference, sort_keys=True).encode()).hexdigest()
        return PreparedCatalogAction(entry, action, instance, self.generation, preference, settings_digest, decision.revision)

    def revalidate(self, prepared):
        entry = self.catalog.get(prepared.entry.id)
        if prepared.generation != self.generation or entry.fingerprint != prepared.entry.fingerprint:
            raise PermissionError('Preparo antigo ou identidade trocada.')
        decision = self.mode(entry, prepared.action)
        if decision.mode == 'block' or decision.revision != prepared.permission_revision:
            raise PermissionError('Autorização revogada/alterada desde o preparo.')
        if prepared.instance is not None and prepared.instance not in self.backend.windows(entry):
            raise PermissionError('Processo/janela mudou desde o preparo.')
        preference = self.settings.read()['preferences'].get(entry.grant_key) if prepared.action == 'place' else None
        if any(hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() != prepared.settings_digest for value in (preference, prepared.preference)):
            raise PermissionError('Preferência mudou desde o preparo.')

    def execute(self, prepared):
        self.permissions.require_executor({TOOLS[self.group(prepared.action)], 'local_application_' + prepared.action})
        self.revalidate(prepared)
        entry, action = prepared.entry, prepared.action
        if action == 'status':
            return json.dumps(self.windows(entry.id), ensure_ascii=False)
        self.kill_switch.require_available()
        if action == 'open':
            windows = self.backend.windows(entry)
            if windows:
                if len(windows) > 1 and prepared.instance is None:
                    raise ApplicationChoiceRequired('Múltiplas janelas: escolha explícita obrigatória.')
                return 'Aplicativo já aberto; nenhuma instância duplicada criada.'
            self.revalidate(prepared)
            self.backend.launch(entry)
            self._wait(lambda: bool(self.backend.windows(entry)), 'Abertura solicitada, mas janela compatível não foi verificada.')
            return 'Aplicativo aberto; janela compatível verificada. Prontidão interna não comprovada.'
        rectangle, fallback = None, False
        if action == 'place':
            rectangle, fallback = preferred_rectangle(prepared.preference, self.backend.monitors())
        self.revalidate(prepared)
        self.kill_switch.require_available()
        self.backend.perform(action, prepared.instance, rectangle)
        if action == 'close':
            self._wait(lambda: prepared.instance not in self.backend.windows(entry),
                'Fechamento gracioso não verificado; salvar/descartar não foi respondido.')
            return 'Janela selecionada fechada de forma graciosa e verificada.'
        self._wait(lambda: self._verify_window(prepared, rectangle), 'Resultado da janela não foi verificado.')
        return 'Operação de janela verificada.' + (' Monitor preferido indisponível; usada área útil do monitor disponível.' if fallback else '')

    def _verify_window(self, prepared, rectangle):
        self.revalidate(prepared)
        return self.backend.verified(prepared.action, prepared.instance, rectangle)

    def _wait(self, predicate, error):
        deadline = self.clock() + self.verification_timeout
        while not predicate():
            if self.clock() >= deadline:
                raise RuntimeError(error)
            self.sleep(min(0.05, self.verification_timeout))
