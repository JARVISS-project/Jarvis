"""Explicit linear plans over the existing executor, without inferred authority."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from threading import Event, RLock
import unicodedata
from uuid import uuid4

from jarvis.remote.auth import _atomic_write_json


KINDS = {'calendar', 'briefing', 'app', 'destination', 'media', 'datetime'}
TEMPLATES = (
    ('day', 'Começar meu dia', 'comecar meu dia'),
    ('study', 'Modo estudo', 'modo estudo'),
    ('mine', 'Minha rotina', 'minha rotina'),
)


def normalize(value: str) -> str:
    text = ''.join(c for c in unicodedata.normalize('NFKD', value.lower())
                   if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9/ -]', ' ', text).split())


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class RoutineService:
    def __init__(self, harness) -> None:
        self.harness = harness
        self.path = harness.config.settings_path.parent / 'routines.json'
        self.history_path = self.path.with_name('routine-runs.json')
        self.lock = RLock()
        self.cancelled = Event()
        self.active = None
        self.tokens = {}
        self.progress = lambda message: None
        self.history = self._load(self.history_path, [])
        if not isinstance(self.history, list) or len(self.history) > 100:
            raise ValueError('Histórico de rotinas inválido.')
        for run in self.history:
            if (not isinstance(run, dict) or set(run) != {'id', 'routine', 'status', 'steps'}
                    or not re.fullmatch(r'[a-f0-9]{32}', run['id'])
                    or not re.fullmatch(r'[a-z0-9-]{1,40}', run['routine'])
                    or run['status'] not in {'running', 'confirmation', 'executing', 'interrupted', 'cancelled', 'success'}
                    or not isinstance(run['steps'], list) or len(run['steps']) > 12):
                raise ValueError('Histórico de rotinas inválido.')
            for stage in run['steps']:
                if (set(stage) != {'id', 'status'} or not re.fullmatch(r'[a-f0-9]{32}', stage['id'])
                        or stage['status'] not in {'pending', 'executing', 'success', 'error', 'denied', 'security_blocked', 'not_found', 'confirmation_required'}):
                    raise ValueError('Recibo de etapa inválido.')
            if run['status'] in {'running', 'confirmation', 'executing'}:
                run['status'] = 'interrupted'
        if self.history:
            _atomic_write_json(self.history_path, self.history)
        permissions = getattr(harness.tool_executor, 'permissions', None)
        if permissions is not None:
            permissions.protected_paths.update((self.path.resolve(), self.history_path.resolve()))

    @staticmethod
    def _load(path: Path, default):
        try:
            from jarvis.security.permissions import PermissionStore
            return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=PermissionStore._pairs)
        except FileNotFoundError:
            return deepcopy(default)

    def definitions(self) -> list[dict]:
        default = [dict(id=i, name=n, command=c, enabled=False, steps=[])
                   for i, n, c in TEMPLATES]
        value = self._load(self.path, default)
        if not isinstance(value, list) or len(value) > 32:
            raise ValueError('Definições inválidas; preserve o arquivo para diagnóstico.')
        names, commands, ids = set(), set(), set()
        for row in value:
            self.validate(row)
            if normalize(row['name']) in names or row['command'] in commands or row['id'] in ids:
                raise ValueError('Nome/comando duplicado.')
            names.add(normalize(row['name']))
            commands.add(row['command'])
            ids.add(row['id'])
        return value

    @staticmethod
    def validate(row: dict) -> None:
        if not isinstance(row, dict) or set(row) != {'id', 'name', 'command', 'enabled', 'steps'}:
            raise ValueError('Definição inválida.')
        if not isinstance(row['id'], str) or not re.fullmatch(r'[a-z0-9-]{1,40}', row['id']):
            raise ValueError('Identificador inválido.')
        if not isinstance(row['name'], str) or not 1 <= len(row['name'].strip()) <= 60:
            raise ValueError('Nome inválido.')
        if not isinstance(row['command'], str) or row['command'] != normalize(row['command']):
            raise ValueError('Comando deve ser normalizado.')
        canonical = dict((i, c) for i, _n, c in TEMPLATES)
        if row['command'] != canonical.get(row['id']) and not re.fullmatch(r'rotina [a-z0-9 ]{1,50}', row['command']):
            raise ValueError('Use comando exclusivo “rotina nome”; comandos existentes são reservados.')
        if type(row['enabled']) is not bool or not isinstance(row['steps'], list) or len(row['steps']) > 12:
            raise ValueError('No máximo doze etapas lineares.')
        for step in row['steps']:
            if not isinstance(step, dict) or set(step) != {'kind', 'resource', 'identity'}:
                raise ValueError('Etapa inválida.')
            if step['kind'] not in KINDS or any(not isinstance(step[k], str) or len(step[k]) > 150 for k in ('resource', 'identity')):
                raise ValueError('Etapa fora do escopo.')

    def save(self, row: dict, revision: str) -> dict:
        with self.lock:
            rows = self.definitions()
            if digest(rows) != revision:
                raise ValueError('Definições mudaram; recarregue.')
            self.validate(row)
            updated = [r for r in rows if r['id'] != row['id']] + [deepcopy(row)]
            if any(normalize(r['name']) == normalize(row['name']) or r['command'] == row['command']
                   for r in rows if r['id'] != row['id']):
                raise ValueError('Nome/comando já utilizado.')
            if len(updated) > 32:
                raise ValueError('Limite de rotinas.')
            _atomic_write_json(self.path, updated)
            self.tokens.clear()
            if self.active:
                self.cancelled.set()
            return self.inventory()

    def delete(self, identifier: str, revision: str) -> dict:
        with self.lock:
            rows = self.definitions()
            if digest(rows) != revision:
                raise ValueError('Definições mudaram.')
            _atomic_write_json(self.path, [r for r in rows if r['id'] != identifier])
            self.tokens.clear()
            if self.active:
                self.cancelled.set()
            return self.inventory()

    def resolve(self, step: dict):
        h = self.harness
        kind, resource = step['kind'], step['resource']
        if kind == 'calendar':
            if not h.config.google_calendar_enabled or not resource:
                raise PermissionError('Escolha agenda conectada e autorizada em Contas e conexões.')
            tool = h.tool_registry.get('google_calendar_list_events')
            return tool, {'period': 'today', 'calendar_id': tool.resolve_calendar(resource)}
        if kind == 'briefing':
            if resource != 'local' or not h.config.briefing_enabled:
                raise PermissionError('Habilite briefing e escolha memória local como fonte.')
            return h._briefing_memory_tool, {}
        tool_name = {'app': 'app_catalog_open', 'destination': 'safe_browser_open',
                     'media': 'local_media_control', 'datetime': 'get_datetime'}[kind]
        tool = h.tool_registry.get(tool_name)
        if tool is None:
            raise PermissionError('Capacidade indisponível; confira Ajuda.')
        if kind == 'app':
            entry = tool.service.catalog.get(resource)
            if entry is None or entry.fingerprint != step['identity']:
                raise PermissionError('Identidade do aplicativo mudou; selecione novamente em Aplicativos.')
            return tool, {'application': resource}
        if kind == 'destination':
            return tool, {'destination': resource}
        if kind == 'media':
            if resource not in {'play', 'pause', 'next', 'previous'}:
                raise ValueError('Escolha controle de mídia disponível.')
            return tool, {'action': resource}
        return tool, {}

    def check(self, step: dict) -> str:
        tool, arguments = self.resolve(step)
        if tool is None:
            raise PermissionError('Fonte indisponível.')
        executor = self.harness.tool_executor
        from jarvis.tools.executor import ToolExecutor
        if not isinstance(executor, ToolExecutor):
            raise PermissionError('Executor com recibos indisponível.')
        if executor.capability_policy is not None:
            executor.capability_policy.require_tool(tool.name)
        permission = executor._permission_check(tool, arguments)
        if permission is not None and permission.mode == 'block':
            raise PermissionError('Bloqueada por permissão; revise Permissões.')
        executor._validate_arguments(tool, arguments)
        preparation = tool.prepare(**arguments)
        tool.preflight_prepared(preparation, **arguments)
        if step['kind'] == 'calendar':
            return f'Consultar agenda “{step["resource"]}” de hoje, no fuso da fonte calculado ao executar'
        if step['kind'] == 'briefing':
            return 'Ler tarefas/lembretes explícitos e autorizados da memória local'
        if step['kind'] == 'destination':
            return f'Abrir destino registrado “{step["resource"]}” no navegador padrão'
        if step['kind'] == 'media':
            action = {'play': 'reproduzir', 'pause': 'pausar', 'next': 'próxima faixa', 'previous': 'faixa anterior'}[step['resource']]
            return f'Mídia local: {action}'
        if step['kind'] == 'datetime':
            return 'Consultar data e hora atuais'
        return preparation.target

    def inventory(self) -> dict:
        rows = self.definitions()
        choices = [{'kind': 'datetime', 'resource': '', 'identity': '', 'label': 'Data e hora'},
                   {'kind': 'briefing', 'resource': 'local', 'identity': '', 'label': 'Briefing: memória local autorizada'}]
        for name, kind, arg in [('safe_browser_open', 'destination', 'destination'), ('local_media_control', 'media', 'action')]:
            tool = self.harness.tool_registry.get(name)
            if tool:
                for resource in tool.parameters['properties'][arg].get('enum', []):
                    if kind == 'media' and resource not in {'play', 'pause', 'next', 'previous'}:
                        continue
                    title = ('Abrir no navegador: ' + resource if kind == 'destination' else
                             'Mídia: ' + {'play': 'reproduzir', 'pause': 'pausar',
                                         'next': 'próxima faixa', 'previous': 'faixa anterior'}[resource])
                    choices.append(dict(kind=kind, resource=resource, identity='', label=title))
        tool = self.harness.tool_registry.get('google_calendar_list_events')
        if tool:
            for alias in getattr(tool, 'calendar_aliases', {}):
                choices.append(dict(kind='calendar', resource=alias, identity='', label=f'Agenda hoje: {alias}'))
        tool = self.harness.tool_registry.get('app_catalog_open')
        if tool:
            for entry in tool.service.catalog.entries():
                choices.append(dict(kind='app', resource=entry.id, identity=entry.fingerprint, label=f'Abrir: {entry.name}'))
        return {'rows': rows, 'revision': digest(rows), 'choices': choices,
                'history': deepcopy(self.history[-30:])}

    def preview(self, identifier: str) -> dict:
        with self.lock:
            row = next(r for r in self.definitions() if r['id'] == identifier)
            lines, ready = [], row['enabled'] and bool(row['steps'])
            for i, step in enumerate(row['steps'], 1):
                try:
                    title = self.check(step)
                    lines.append(f'{i}. {title}; confirmação/permissão será revalidada.')
                except Exception as error:
                    ready = False
                    lines.append(f'{i}. Pendente: {str(error) if isinstance(error, (PermissionError, ValueError)) else "configure a fonte em sua área"}')
            token = uuid4().hex
            self.tokens[token] = deepcopy(row)
            if len(self.tokens) > 64:
                self.tokens.pop(next(iter(self.tokens)))
            return {'token': token, 'ready': ready, 'text': '\n'.join([row['name'], *lines,
                'Rotina desabilitada/vazia: edite e habilite explicitamente.' if not row['enabled'] or not row['steps'] else
                'Cada etapa usa o executor; cancelar não desfaz efeitos realizados.'])}

    def permission_revision(self):
        permissions = getattr(self.harness.tool_executor, 'permissions', None)
        if permissions is None:
            return None
        _rules, revision, valid = permissions.read()
        if not valid:
            raise PermissionError('Permissões inválidas.')
        return revision

    def valid_active(self) -> bool:
        if not self.active:
            return False
        try:
            row = next((r for r in self.definitions() if r['id'] == self.active['plan']['id']), None)
            return (not self.cancelled.is_set() and row == self.active['plan']
                    and digest(row) == self.active['digest']
                    and self.permission_revision() == self.active['permissions'])
        except Exception:
            return False

    def start(self, token: str) -> str:
        if not self.lock.acquire(blocking=False):
            return 'Runtime ocupado; execução não enfileirada.'
        try:
            return self._start_locked(token)
        finally:
            self.lock.release()

    def _start_locked(self, token: str) -> str:
        with self.lock:
            if self.active or self.harness.has_pending_action():
                return 'Uma rotina já está ativa; cancele ou conclua a confirmação.'
            plan = self.tokens.pop(token, None)
            if plan is None or not plan['enabled'] or not plan['steps']:
                return 'Prévia antiga, desabilitada ou vazia; abra Rotinas e prepare novamente.'
            current = next((r for r in self.definitions() if r['id'] == plan['id']), None)
            if current != plan:
                return 'Plano mudou; prepare nova prévia.'
            from jarvis.tools.executor import ToolExecutor
            if not isinstance(self.harness.tool_executor, ToolExecutor):
                return 'Executor com recibos indisponível.'
            run = {'id': uuid4().hex, 'routine': plan['id'], 'status': 'running',
                   'steps': [{'id': uuid4().hex, 'status': 'pending'} for s in plan['steps']]}
            self.cancelled.clear()
            self.active = {'plan': deepcopy(plan), 'digest': digest(plan), 'permissions': self.permission_revision(),
                           'run': run, 'index': 0, 'texts': []}
            self.history.append(run)
            self.history = self.history[-100:]
            return self.advance()

    def record(self):
        _atomic_write_json(self.history_path, self.history)

    def finish(self, status: str) -> str:
        active = self.active
        active['run']['status'] = status
        self.record()
        self.active = None
        title = {'success': 'concluída', 'cancelled': 'cancelada', 'interrupted': 'interrompida'}[status]
        return '\n'.join([f'Rotina {title}. Efeitos realizados não são desfeitos.', *active['texts']])

    def consume(self, result) -> str | None:
        active = self.active
        stage = active['run']['steps'][active['index']]
        stage['status'] = result.status
        if result.status == 'confirmation_required':
            active['run']['status'] = 'confirmation'
            self.record()
            return result.content
        if result.status != 'success' or not result.authorized:
            return self.finish('interrupted')
        active['texts'].append(result.content)
        active['index'] += 1
        self.record()
        return None

    def advance(self) -> str:
        while self.active and self.active['index'] < len(self.active['plan']['steps']):
            if not self.valid_active():
                return self.finish('cancelled')
            active = self.active
            step = active['plan']['steps'][active['index']]
            stage = active['run']['steps'][active['index']]
            stage['status'] = 'executing'
            active['run']['status'] = 'executing'
            self.record()  # Durable fence BEFORE executor; never retry after restart.
            self.progress(f'Rotina: etapa {active["index"] + 1}/{len(active["plan"]["steps"])} em execução.')
            try:
                tool, arguments = self.resolve(step)
                response = self.consume(self.harness._execute_tool(tool, arguments, interaction_id=stage['id']))
            except Exception:
                response = self.finish('interrupted')
            if response is not None:
                return response
        return self.finish('success')

    def cancel(self) -> None:
        self.cancelled.set()

    def confirmation_valid(self) -> bool:
        if not self.active:
            return True
        session = self.harness.confirmation_sessions.current()
        stage = self.active['run']['steps'][self.active['index']]
        if not self.valid_active() or session is None:
            return False
        try:
            tool, arguments = self.resolve(self.active['plan']['steps'][self.active['index']])
        except Exception:
            return False
        return (session.invocation.tool_name == tool.name
                and digest(dict(session.invocation.arguments)) == digest(arguments)
                and session.invocation.interaction_id == stage['id']
                and stage['status'] == 'confirmation_required')

    def resume(self, result) -> str:
        response = self.consume(result)
        return response if response is not None else self.advance()

    def dispatch(self, message: str) -> str | None:
        value = normalize(message)
        if value not in {c for _i, _n, c in TEMPLATES} and not value.startswith(('rotina ', '/rotina ')):
            return None
        if value == '/rotina cancelar':
            self.cancel()
            self.harness.cancel_pending_confirmation()
            with self.lock:
                return self.finish('cancelled') if self.active else 'Nenhuma rotina ativa.'
        if value.startswith('/rotina executar '):
            if message.rstrip().endswith('?'):
                return 'Consulte a prévia em Rotinas; perguntas não executam etapas.'
            return self.start(value.removeprefix('/rotina executar '))
        row = next((r for r in self.definitions() if r['command'] == value), None)
        if row:
            preview = self.preview(row['id'])
            return self.start(preview['token']) if preview['ready'] and not message.rstrip().endswith('?') else preview['text']
        if value.startswith(('rotina ', '/rotina ')):
            return 'Rotina não encontrada; configure um comando exclusivo em Rotinas.'
        return None
