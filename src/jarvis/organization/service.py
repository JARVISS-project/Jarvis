"""Selected-root plans and reversible journals; metadata is never authority."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from threading import Event, RLock
from uuid import uuid4

from jarvis.integrations.google.operation_lock import GoogleOperationLock
from jarvis.remote.auth import _atomic_write_json
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.permissions import PermissionDecision, PermissionStore, scoped_resource
from jarvis.organization.windows import WindowsMoves, safe_path

CATEGORIES = {
    'Documentos': ['.pdf', '.txt', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.csv'],
    'Imagens': ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'],
    'Vídeos': ['.mp4', '.mkv', '.avi', '.mov', '.webm'],
    'Áudio': ['.mp3', '.wav', '.flac', '.ogg', '.m4a'],
    'Compactados': ['.zip', '.7z', '.rar', '.tar', '.gz'],
    'Instaladores': ['.exe', '.msi', '.msix'],
}
MAX_FILES, MAX_ENTRIES, MAX_BYTES = 128, 2048, 256 * 1024 * 1024
ID = re.compile(r'^[0-9a-f]{32}$')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def valid_name(name: str) -> None:
    if not name or len(name.encode('utf-16-le')) // 2 > 200 or name[-1] in '. ' or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name):
        raise ValueError('Nome inválido ou longo no Windows.')
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', name.split('.')[0], re.I) or name in {'.', '..'}:
        raise ValueError('Nome reservado no Windows.')


def default_rules():
    return {'schema': 1, 'extensions': {k: list(v) for k, v in CATEGORIES.items()}, 'date_group': False,
        'recursive': False, 'rename': '', 'collision': 'review'}


def validate_rules(value):
    if not isinstance(value, dict) or set(value) != set(default_rules()) or type(value['schema']) is not int or value['schema'] != 1:
        raise ValueError('Schema de regras inválido.')
    if type(value['recursive']) is not bool or type(value['date_group']) is not bool or value['collision'] not in {'review', 'suffix'}:
        raise ValueError('Recursão/data/conflitos inválidos.')
    if not isinstance(value['extensions'], dict) or set(value['extensions']) != set(CATEGORIES):
        raise ValueError('Categorias inválidas.')
    seen = set()
    for extensions in value['extensions'].values():
        if not isinstance(extensions, list) or len(extensions) > 64:
            raise ValueError('Extensões inválidas.')
        for extension in extensions:
            if not isinstance(extension, str) or not re.fullmatch(r'\.[a-z0-9]{1,10}', extension) or extension in seen:
                raise ValueError('Extensão inválida/duplicada.')
            seen.add(extension)
    template = value['rename']
    if not isinstance(template, str) or len(template) > 100 or re.search(r'\{(?!name\}|date\}|n\})|(?<!name)(?<!date)(?<!n)\}', template):
        raise ValueError('Template suporta somente {name}, {date}, {n}.')
    if template:
        rendered = template.replace('{name}', 'arquivo').replace('{date}', '2026-10-01').replace('{n}', '1')
        valid_name(rendered)
    return json.loads(json.dumps(value))


class PrivateJournal:
    """Atomic HMAC-sealed local metadata; key/payload never enter ordinary audit."""
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        safe_path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        key = self.directory / 'integrity.key'
        if not key.exists():
            if any(self.directory.glob('*.json')):
                raise PermissionError('Chave do journal ausente; recuperação requer revisão.')
            try:
                with key.open('xb') as stream:
                    stream.write(secrets.token_bytes(32))
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
        self.key = key.read_bytes()
        if len(self.key) != 32:
            raise PermissionError('Chave do journal inválida.')

    def read(self, name, default=None):
        path = self.directory / (name + '.json')
        if not path.exists():
            return default
        if path.stat().st_size > 2 * 1024 * 1024:
            raise PermissionError('Journal excede limite.')
        value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=PermissionStore._pairs)
        if not isinstance(value, dict) or set(value) != {'schema', 'payload', 'mac'} or type(value['schema']) is not int or value['schema'] != 1:
            raise PermissionError('Journal inválido.')
        expected = hmac.new(self.key, digest(value['payload']).encode(), hashlib.sha256).hexdigest()
        if not isinstance(value['mac'], str) or not hmac.compare_digest(expected, value['mac']):
            raise PermissionError('Journal alterado; nenhuma autoridade concedida.')
        return value['payload']

    def save(self, name, value):
        with self._lock:
            mac = hmac.new(self.key, digest(value).encode(), hashlib.sha256).hexdigest()
            _atomic_write_json(self.directory / (name + '.json'), {'schema': 1, 'payload': value, 'mac': mac})


class OrganizationService:
    def __init__(self, directory: Path, permissions, backend=None):
        self.store = PrivateJournal(directory)
        self.permissions = permissions
        self.backend = backend or WindowsMoves()
        self._cancel = Event()
        self._cancel_generation = 0
        self.after_operation = lambda _result: None
        self._lock = RLock()
        permissions.protected_paths.add(self.store.directory)
        self.selected = None
        state = self.store.read('selection', {'schema': 1, 'roots': {}, 'selected': None})
        if set(state) != {'schema', 'roots', 'selected'} or state['schema'] != 1 or not isinstance(state['roots'], dict) or len(state['roots']) > 20:
            raise PermissionError('Seleções inválidas.')
        self.state = state
        self.selected = state['selected']
        for value in state['roots'].values():
            root = Path(value['path'])
            try:
                if self.backend.root_identity(root) == value['identity']:
                    self._register_policy_root(root)
            except (OSError, ValueError, PermissionError):
                pass  # Stale selections never enlarge operational authority.

    def _register_policy_root(self, root):
        # Only permits administrative path restrictions; Tools retain their own guards.
        resolved = root.resolve()
        if resolved not in self.permissions.roots:
            self.permissions.roots += (resolved,)

    def select(self, path: str):
        root = Path(path)
        if not self.permissions.read()[2]:
            raise PermissionError('Configuração de permissões inválida.')
        identity = self.backend.root_identity(root)
        if root.parent == root or root == self.store.directory or self.store.directory.is_relative_to(root):
            raise PermissionError('Raiz de volume/armazenamento administrativo não organizável.')
        self._register_policy_root(root)
        if self.permissions.path_decision(root, 'read') != 'allow':
            raise PermissionError('Leitura de metadados exige autorização de caminho.')
        with self._lock:
            if len(self.state['roots']) >= 20:
                raise ValueError('Limite de 20 seleções locais.')
            token = uuid4().hex
            self.state['roots'][token] = {'path': str(root.resolve()), 'identity': identity, 'rules': default_rules()}
            self.state['selected'] = self.selected = token
            self.store.save('selection', self.state)
            return self.describe()

    def root(self, token=None):
        token = token or self.selected
        if not isinstance(token, str) or not ID.fullmatch(token) or token not in self.state['roots']:
            raise PermissionError('Escolha uma pasta na UI; Downloads não é autorização automática.')
        value = self.state['roots'][token]
        root = Path(value['path'])
        if self.backend.root_identity(root) != value['identity']:
            raise PermissionError('Identidade da pasta mudou; selecione novamente.')
        validate_rules(value['rules'])
        return token, root, value

    def describe(self):
        if not self.selected:
            return {'selected': None, 'rules': default_rules(), 'explanation': 'Escolha uma pasta. Downloads é apenas sugestão.'}
        token, root, value = self.root()
        return {'selected': token, 'folder': str(root), 'rules': value['rules'], 'revision': self.permissions.read()[1],
            'policy': self.decision('organization_execute', token).mode,
            'explanation': 'Data = última modificação do filesystem, em UTC; não inferida do nome. Hash de integridade não interpreta nem indexa conteúdo. Recursão: até 5 níveis; 128 movimentos/2048 entradas/256 MiB por análise.'}

    def rules(self, token, value):
        self.root(token)
        self.state['roots'][token]['rules'] = validate_rules(value)
        self.store.save('selection', self.state)
        return self.describe()

    def scope(self, tool, token):
        _, root, value = self.root(token)
        key = digest({'root': str(root), 'identity': value['identity'], 'rules': value['rules']})
        return scoped_resource(tool, 'organization', key)

    def decision(self, tool, token):
        scoped = self.permissions.decision(self.scope(tool, token), default='ask')
        global_mode = self.permissions.decision('tool:' + tool).mode
        mode = 'block' if 'block' in {scoped.mode, global_mode} else 'ask' if 'ask' in {scoped.mode, global_mode} else 'allow'
        return PermissionDecision(mode, scoped.revision)

    def grant(self, token, action, mode, revision):
        if action not in {'organization_execute', 'organization_undo'}:
            raise ValueError('Ação não suportada.')
        self.permissions.set_rule(self.scope(action, token), 'use', mode, expected_revision=revision)
        return self.describe()

    def _guard(self, root, relative, action='read'):
        path = Path(relative)
        if path.is_absolute() or any(part in {'.', '..'} for part in path.parts):
            raise PermissionError('Travessia bloqueada.')
        for part in path.parts:
            valid_name(part)
        candidate = root / path
        safe_path(candidate)
        guard = FileSystemGuard(root, self.permissions)
        result = guard.resolve(relative, for_write=action == 'write')
        if self.permissions.path_decision(candidate, action) == 'block':
            raise PermissionError('Caminho bloqueado.')
        if action == 'read' and self.permissions.path_decision(candidate, 'read') != 'allow':
            raise PermissionError('Leitura de integridade bloqueada ou exige consentimento de caminho.')
        if candidate.name.casefold().startswith('.env') or any(word in candidate.name.casefold() for word in ('credential', 'token', 'secret')) or candidate.suffix.casefold() in {'.pem', '.key', '.pfx', '.p12'}:
            raise PermissionError('Nome sensível não indexável.')
        return result

    def analyze(self, token):
        _, root, value = self.root(token)
        rules = value['rules']
        rows, ignored, visited, total_bytes, chosen = [], [], 0, 0, set()
        def scan(directory, depth=0):
            nonlocal visited
            for entry in sorted(directory.iterdir(), key=lambda p: (p.name.casefold(), p.name)):
                visited += 1
                if visited > MAX_ENTRIES:
                    raise ValueError('Limite de 2048 entradas; escolha escopo menor.')
                relative = entry.relative_to(root).as_posix()
                try:
                    self._guard(root, relative)
                    if entry.is_dir():
                        if rules['recursive'] and depth < 5 and entry.name not in CATEGORIES:
                            yield from scan(entry, depth + 1)
                        else:
                            ignored.append({'source': relative, 'reason': 'Pasta preservada/fora da recursão limitada.'})
                    elif entry.is_file():
                        yield entry
                    else:
                        ignored.append({'source': relative, 'reason': 'Tipo não regular.'})
                except (ValueError, OSError, PermissionError):
                    ignored.append({'source': relative, 'reason': 'Protegido/link/nome/acesso não suportado.'})
        for path in scan(root):
            source = path.relative_to(root).as_posix()
            category = next((name for name, extensions in rules['extensions'].items() if path.suffix.casefold() in extensions), None)
            if category is None:
                ignored.append({'source': source, 'reason': 'Extensão desconhecida; revisão, permanece no lugar.'})
                continue
            if len(rows) >= MAX_FILES or total_bytes + path.stat().st_size > MAX_BYTES:
                ignored.append({'source': source, 'reason': 'Limite do lote/integridade; revisão.'})
                continue
            try:
                identity = self.backend.inspect(path)
                date = datetime.fromtimestamp(identity['mtime'] / 1e9, timezone.utc)
                name = path.name
                if rules['rename']:
                    name = rules['rename'].replace('{name}', path.stem).replace('{date}', date.strftime('%Y-%m-%d')).replace('{n}', str(len(rows) + 1)) + path.suffix
                valid_name(name)
                destination = Path(category) / (date.strftime('%Y-%m') if rules['date_group'] else '') / name
                for suffix in range(101):
                    candidate = root / destination
                    collision = str(destination).casefold() in chosen or (candidate.parent.exists() and any(p.name.casefold() == candidate.name.casefold() for p in candidate.parent.iterdir()))
                    if not collision:
                        break
                    if rules['collision'] == 'review' or suffix == 100:
                        raise FileExistsError('Conflito; revisão.')
                    destination = destination.with_name(Path(name).stem + f' ({suffix + 1})' + Path(name).suffix)
                self._guard(root, str(destination), 'write')
                self._guard(root, source, 'write')
                rows.append({'id': uuid4().hex, 'source': source, 'destination': destination.as_posix(), 'identity': identity,
                    'category': category, 'reason': 'Extensão explícita; data de modificação UTC.' if rules['date_group'] else 'Extensão explícita.',
                    'renamed': name != path.name or destination.name != path.name, 'state': 'planned', 'undo': 'planned', 'error': ''})
                total_bytes += identity['size']
                chosen.add(str(destination).casefold())
            except (OSError, ValueError, PermissionError):
                ignored.append({'source': source, 'reason': 'Conflito/proteção/nome/arquivo em uso ou identidade não comprovada; revisão.'})
        plan = {'schema': 1, 'id': uuid4().hex, 'root': token, 'rules': rules, 'root_identity': value['identity'],
            'revision': self.permissions.read()[1], 'created': datetime.now(timezone.utc).isoformat(),
            'operations': rows, 'ignored': ignored, 'executed': False, 'cancelled': False}
        if len(list(self.store.directory.glob('plan-*.json'))) >= 200:
            raise ValueError('Histórico local atingiu limite de 200 planos; não removido automaticamente.')
        self.store.save('plan-' + plan['id'], plan)
        return plan

    def load(self, plan_id):
        if not isinstance(plan_id, str) or not ID.fullmatch(plan_id):
            raise ValueError('ID de plano inválido.')
        plan = self.store.read('plan-' + plan_id)
        if not isinstance(plan, dict) or plan.get('schema') != 1 or plan.get('id') != plan_id:
            raise PermissionError('Plano ausente/inválido.')
        return plan

    def view(self, plan_id):
        plan = self.load(plan_id)
        _, root, _ = self.root(plan['root'])
        if self.decision('organization_execute', plan['root']).mode == 'block':
            raise PermissionError('Plano bloqueado pela revogação do escopo.')
        for row in plan['operations']:
            self._guard(root, row['source'])
            self._guard(root, row['destination'])
        return plan

    def prepare(self, plan_id, undo=False):
        plan = self.load(plan_id)
        token, root, value = self.root(plan['root'])
        if plan['rules'] != value['rules'] or plan['root_identity'] != value['identity']:
            raise PermissionError('Pasta/regras mudaram; nova análise necessária.')
        decision = self.decision('organization_undo' if undo else 'organization_execute', token)
        modes = {decision.mode}
        for row in plan['operations']:
            for path in (row['source'], row['destination']):
                modes.add(self.permissions.path_decision(root / path, 'write'))
        if 'block' in modes:
            raise PermissionError('Caminho do plano bloqueado.')
        if decision.mode == 'block':
            raise PermissionError('Organização bloqueada.')
        if not undo and not plan['executed']:
            if plan['revision'] != decision.revision:
                raise PermissionError('Permissões mudaram desde a análise; revise um novo plano.')
            for row in plan['operations']:
                path = self._guard(root, row['source'], 'write')
                self._guard(root, row['destination'], 'write')
                if self.backend.inspect(path) != row['identity']:
                    raise PermissionError('Arquivo alterado; plano desatualizado, revise.')
        return {'id': plan_id, 'undo': undo, 'root': token, 'policy': decision.revision, 'cancel_generation': self._cancel_generation,
            'binding': digest({'id': plan_id, 'root': token, 'rules': plan['rules'], 'operations': [{k: v for k, v in row.items() if k not in {'state', 'undo', 'error'}} for row in plan['operations']], 'undo': undo})}

    def revalidate(self, prepared):
        current = self.prepare(prepared['id'], prepared['undo'])
        if current != prepared:
            raise PermissionError('Plano/permissões mudaram desde o preparo.')

    def cancel(self):
        with self._lock:
            self._cancel_generation += 1
            self._cancel.set()
        return {'message': 'Cancelamento solicitado antes da próxima operação; efeitos anteriores permanecem no histórico.'}

    def execute(self, prepared, tool):
        self.permissions.require_executor({tool.name})
        self.revalidate(prepared)
        plan = self.load(prepared['id'])
        _, root, _ = self.root(plan['root'])
        with GoogleOperationLock(self.store.directory / 'batch.lock', 'Local\\JARVIS.Organization.' + digest(str(self.store.directory))[:24]):
            if not prepared['undo'] and plan['executed']:
                self.reconcile(plan)
                return json.dumps(self.result(plan), ensure_ascii=False)
            with self._lock:
                if prepared['cancel_generation'] != self._cancel_generation:
                    raise PermissionError('Cancelado antes de iniciar o lote.')
                self._cancel.clear()
            plan['executed'] = True
            self.store.save('plan-' + plan['id'], plan)
            rows = list(reversed(plan['operations'])) if prepared['undo'] else plan['operations']
            for row in rows:
                state = 'undo' if prepared['undo'] else 'state'
                if self._cancel.is_set():
                    plan['cancelled'] = True
                    break
                if (prepared['undo'] and row['state'] != 'completed') or row[state] != ('planned' if prepared['undo'] else 'planned'):
                    continue
                source, destination = (row['destination'], row['source']) if prepared['undo'] else (row['source'], row['destination'])
                try:
                    with self.permissions.effect(tool, {'plan': plan['id']}, prepared['policy'], per_operation=True):
                        self.revalidate(prepared)
                        source_path = self._guard(root, source, 'write')
                        destination_path = self._guard(root, destination, 'write')
                        for path in (source_path, destination_path):
                            self.permissions.require_operation(tool.name, path, 'write')
                        def check():
                            if self._cancel.is_set():
                                raise PermissionError('Cancelado antes do efeito.')
                            self.revalidate(prepared)
                            row[state] = 'started'
                            self.store.save('plan-' + plan['id'], plan)
                        self.backend.move(root, source_path, destination_path, row['identity'], check)
                        row[state] = 'completed'
                except (OSError, ValueError, PermissionError, RuntimeError) as error:
                    if row[state] != 'started' or isinstance(error, (OSError, ValueError, PermissionError)):
                        row[state] = 'failed'
                    row['error'] = 'Pendente: conflito, bloqueio, alteração ou resultado incerto. Reconcilie/revise; sem retry cego.'
                self.store.save('plan-' + plan['id'], plan)
                self.reconcile(plan)
                self.after_operation(self.result(plan))
                if self.permissions.read()[1] != prepared['policy']:
                    break
            self.store.save('plan-' + plan['id'], plan)
            return json.dumps(self.result(plan), ensure_ascii=False)

    def reconcile(self, plan):
        _, root, _ = self.root(plan['root'])
        for row in plan['operations']:
            for state in ('state', 'undo'):
                if row[state] != 'started':
                    continue
                source, destination = (row['destination'], row['source']) if state == 'undo' else (row['source'], row['destination'])
                try:
                    src = self._guard(root, source)
                    dst = self._guard(root, destination)
                    verified = not src.exists() and self.backend.inspect(dst) == row['identity']
                except (OSError, ValueError, PermissionError):
                    verified = False
                row[state] = 'completed' if verified else 'failed'
                row['error'] = '' if verified else 'Crash/resultado incerto: revisão necessária; nenhuma repetição automática.'
        self.store.save('plan-' + plan['id'], plan)

    @staticmethod
    def result(plan):
        return {'plan': plan['id'], 'completed': sum(row['state'] == 'completed' for row in plan['operations']),
            'undone': sum(row['undo'] == 'completed' for row in plan['operations']), 'cancelled': plan['cancelled'],
            'operations': plan['operations'], 'ignored': plan['ignored'], 'atomic_batch': False}

    def index(self, token, query=''):
        _, root, _ = self.root(token)
        if self.decision('organization_execute', token).mode == 'block':
            raise PermissionError('Índice bloqueado pela revogação do escopo.')
        result = []
        for path in sorted(self.store.directory.glob('plan-*.json'), reverse=True):
            plan = self.load(path.stem[5:])
            if plan['root'] != token:
                continue
            for row in plan['operations']:
                if row['state'] != 'completed':
                    continue
                current = row['source'] if row['undo'] == 'completed' else row['destination']
                try:
                    self._guard(root, current)
                    self._guard(root, row['source'])
                except (OSError, ValueError, PermissionError):
                    continue
                if query.casefold() in current.casefold():
                    result.append({'identity': row['identity'], 'origin': row['source'], 'current': current, 'category': row['category'],
                        'rule': digest(plan['rules']), 'operation': row['id'], 'date': plan['created']})
                if len(result) >= 100:
                    return {'rows': result}
        return {'rows': result}

    def history(self, token):
        _, root, _ = self.root(token)
        if self.permissions.path_decision(root, 'read') != 'allow':
            raise PermissionError('Leitura do histórico bloqueada.')
        if self.decision('organization_execute', token).mode == 'block':
            raise PermissionError('Histórico bloqueado pela revogação do escopo.')
        rows = []
        for path in self.store.directory.glob('plan-*.json'):
            plan = self.load(path.stem[5:])
            if plan['root'] == token:
                rows.append({'id': plan['id'], 'created': plan['created'],
                    'operations': len(plan['operations']), 'executed': plan['executed']})
        return {'rows': sorted(rows, key=lambda row: row['created'], reverse=True)}
