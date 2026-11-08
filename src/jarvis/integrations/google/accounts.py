"""Local, DPAPI-protected account administration. Never an LLM authority source."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Event, RLock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote
from uuid import uuid4

from jarvis.integrations.google import factory
from jarvis.integrations.google.drive_docs.client import grant_digest
from jarvis.integrations.google.errors import (
    GoogleTokenRevokedError, GooglePermissionError, GoogleResourceNotFoundError,
    GoogleRateLimitError, GoogleNetworkError,
)
from jarvis.integrations.google.models import ConnectionState
from jarvis.integrations.google.operation_lock import GoogleOperationLock
from jarvis.integrations.google.storage import ProtectedFileCredentialStore, WindowsDPAPIProtector

PROFILES = {
    'authentication': ('credentials.bin', factory.build_google_auth_manager),
    'calendar_read': ('credentials_calendar_read.bin', factory.build_google_calendar_minimal_read_auth),
    'drive_docs_read': ('credentials_drive_docs_read.bin', factory.build_google_drive_docs_auth),
    'calendar_write': ('credentials_calendar_limited_write.bin', factory.build_google_calendar_limited_write_auth),
    'legacy_calendar_write': ('credentials_write.bin', factory.build_google_auth_manager_with_calendar_write),
}
LABELS = {'authentication': 'Autenticação legada', 'calendar_read': 'Calendar — leitura',
          'drive_docs_read': 'Drive/Docs — leitura selecionada', 'calendar_write': 'Calendar — escrita limitada',
          'legacy_calendar_write': 'Calendar — escrita legada (não ativada aqui)'}
MAX_BYTES = 65536


def _unique(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError('Chave duplicada.')
    return value


def google_identity(auth, operation: str) -> tuple[str, str]:
    """Verified GET, minimal existing scopes; no email/profile scope added."""
    handle = auth.credentials()
    drive = operation == 'drive_docs_read'
    endpoint = ('https://www.googleapis.com/drive/v3/about?fields=user(permissionId)'
                if drive else 'https://openidconnect.googleapis.com/v1/userinfo')
    request = Request(endpoint, headers={'Authorization': 'Bearer ' + handle.access_token})
    try:
        with urlopen(request, timeout=10.0) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        if error.code == 401:
            raise GoogleTokenRevokedError('Credencial rejeitada; reconecte explicitamente.') from None
        raise PermissionError('Identidade Google não verificada; resposta privada omitida.') from None
    except (URLError, OSError, TimeoutError):
        raise RuntimeError('Consulta de identidade indisponível.') from None
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=_unique)
        subject = value['user']['permissionId'] if drive else value['sub']
        if not isinstance(subject, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', subject):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Identidade Google inválida.') from None
    return ('drive.permissionId' if drive else 'google.sub', subject)


def require_legacy_account(operation: str) -> bool:
    """Legacy administrative entry points cannot silently target another account."""
    from jarvis.config import JarvisConfig
    service = AccountService(JarvisConfig())
    if not service.path.exists():
        return False
    state = service._read()
    row = service._row(state, state['defaults'][operation])
    if not row['legacy']:
        raise PermissionError('Este fluxo legado não gerencia a conta padrão selecionada; use Contas e conexões.')
    return True


class AccountService:
    """Versioned registry; credentials remain in existing separate DPAPI stores.

    Migration references legacy files, without copying tokens or broadening scopes.
    Defaults never transfer resource grants. Runtime bindings are immutable until
    restart, while the registry is revalidated before every external request.
    """
    def __init__(self, config, *, directory: Path | None = None, protector=None,
                 auth_factory=None, identity=google_identity, lock_factory=None, calendar_client_factory=None) -> None:
        self.config = config
        self.directory = directory or factory.google_credential_dir()
        self.path = self.directory / 'accounts.bin'
        self.protector = protector or WindowsDPAPIProtector()
        self.auth_factory = auth_factory or (lambda operation, path: PROFILES[operation][1](path))
        self.identity = identity
        self.calendar_client_factory = calendar_client_factory
        name = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:24]
        self.lock_factory = lock_factory or (lambda: GoogleOperationLock(
            self.directory / 'accounts.lock', 'Local\\JARVIS.Accounts.' + name))
        self._lock = RLock()
        self._verified = {}
        self._cancel = Event()

    def _credential(self, row):
        return self.directory / row['credential']

    def _calendar_client(self, auth):
        from jarvis.integrations.google.calendar.client import GoogleCalendarApiClient
        return self.calendar_client_factory(auth) if self.calendar_client_factory else GoogleCalendarApiClient(auth)

    def _material(self, row):
        return ProtectedFileCredentialStore(self._credential(row), self.protector).load()

    def _read(self):
        if not self.path.exists():
            raise PermissionError('Central ainda não inicializada.')
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_BYTES:
                raise ValueError
            plaintext = self.protector.unprotect(raw)
            if len(plaintext) > MAX_BYTES:
                raise ValueError
            state = json.loads(plaintext, object_pairs_hook=_unique)
            self._validate(state)
            return state
        except Exception:
            raise PermissionError('Registro de contas inválido; nenhum acesso adicional concedido.') from None

    @staticmethod
    def _validate(state):
        if (not isinstance(state, dict) or set(state) != {'version', 'revision', 'connections', 'defaults'}
                or type(state['version']) is not int or state['version'] != 1
                or not re.fullmatch(r'[a-f0-9]{32}', str(state['revision']))
                or not isinstance(state['connections'], dict) or len(state['connections']) > 64
                or not isinstance(state['defaults'], dict) or set(state['defaults']) != set(PROFILES)):
            raise ValueError('Schema de contas inválido.')
        for key, row in state['connections'].items():
            if (not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key)
                    or not isinstance(row, dict) or set(row) != {'operation', 'label', 'credential', 'legacy',
                        'namespace', 'subject', 'grant', 'resources', 'epoch'}
                    or row['operation'] not in PROFILES or type(row['legacy']) is not bool
                    or not isinstance(row['label'], str) or not 1 <= len(row['label']) <= 80
                    or any(ord(c) < 32 for c in row['label'])
                    or not isinstance(row['resources'], dict)
                    or not re.fullmatch(r'[a-f0-9]{32}', str(row['epoch']))
                    or row['namespace'] not in {'', 'google.sub', 'drive.permissionId'}
                    or not isinstance(row['subject'], str) or len(row['subject']) > 256
                    or (row['subject'] and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', row['subject']))
                    or (bool(row['subject']) != bool(row['namespace']))
                    or not isinstance(row['grant'], str)
                    or (row['grant'] and not re.fullmatch(r'[a-f0-9]{64}', row['grant']))):
                raise ValueError('Conexão inválida.')
            expected = PROFILES[row['operation']][0] if row['legacy'] else f'account-{key}.bin'
            if row['credential'] != expected:
                raise ValueError('Destino de credencial inválido.')
            if row['legacy'] and key != hashlib.sha256(('legacy:' + row['operation']).encode()).hexdigest()[:32]:
                raise ValueError('Identidade legada inválida.')
            for alias, resource in row['resources'].items():
                if (not isinstance(alias, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', alias)
                        or not isinstance(resource, str) or not resource or len(resource) > 256
                        or any(ord(c) < 32 for c in resource)):
                    raise ValueError('Recurso inválido.')
        for operation, key in state['defaults'].items():
            if key not in state['connections'] or state['connections'][key]['operation'] != operation:
                raise ValueError('Padrão inválido; sem fallback.')

    def _save(self, state):
        state['revision'] = uuid4().hex
        self._validate(state)
        raw = json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError('Limite de contas atingido.')
        protected = self.protector.protect(raw)
        if not protected or len(protected) > MAX_BYTES:
            raise ValueError('Registro protegido excede limite.')
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix='.accounts-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def initialize(self):
        with self._lock, self.lock_factory():
            if self.path.exists():
                self._read()
                return
            state = {'version': 1, 'revision': uuid4().hex, 'connections': {}, 'defaults': {}}
            for operation, (filename, _) in PROFILES.items():
                key = hashlib.sha256(('legacy:' + operation).encode()).hexdigest()[:32]
                resources = (dict(self.config.google_calendar_authorized_calendars) if operation == 'calendar_read' else
                             dict(self.config.google_calendar_write_calendars) if operation == 'calendar_write' else {})
                row = dict(operation=operation, label=LABELS[operation], credential=filename, legacy=True,
                           namespace='', subject='', grant='', resources=resources, epoch=uuid4().hex)
                try:
                    material = self._material(row)
                    if material is not None and not material.revocation_pending:
                        row['grant'] = grant_digest(material)
                except Exception:
                    pass  # Inventory reports failure; no readiness inferred from file presence.
                state['connections'][key] = row
                state['defaults'][operation] = key
            self._save(state)

    def inventory(self):
        self.initialize()
        with self._lock:
            state = self._read()
            rows = []
            for key, row in state['connections'].items():
                status, scopes = 'não conectado', []
                reason = 'Conecte explicitamente; nenhum consentimento operacional implícito.'
                try:
                    material = self._material(row)
                    if material is None and self._verified.get(key, ('',))[0] == 'expirado':
                        status, reason = self._verified[key]
                    if material:
                        scopes = list(material.scopes)
                        if material.revocation_pending or (row['grant'] and grant_digest(material) != row['grant']):
                            status, reason = 'erro', 'Credencial revogada ou grant/identidade alterado; acesso bloqueado.'
                        else:
                            status, reason = self._verified.get(key, ('indisponível', 'Credencial local presente; teste somente leitura necessário.'))
                except Exception:
                    status, reason = 'erro', 'Credencial local inválida; conteúdo omitido.'
                rows.append({'id': key, 'service': 'Google', 'operation': row['operation'], 'label': row['label'],
                    'account': row['namespace'] + ':' + row['subject'] if row['subject'] else 'Identidade ainda não verificada',
                    'account_id': hashlib.sha256((row['namespace'] + ':' + row['subject']).encode()).hexdigest() if row['subject'] else None,
                    'status': status, 'reason': reason, 'scopes': scopes, 'resources': dict(row['resources']),
                    'default': state['defaults'][row['operation']] == key,
                    'legacy': row['legacy']})
            return {'revision': state['revision'], 'rows': rows, 'read_enabled': self.config.google_calendar_enabled,
                    'write_enabled': self.config.google_calendar_write_enabled,
                    'planned': ['Spotify OAuth e redes sociais: planejados; backend de contas indisponível.'],
                    'note': 'Trocar padrão exige reiniciar runtime; não transfere recursos. Desconectar é somente local; não revoga no Google.'}

    def _row(self, state, connection):
        if not isinstance(connection, str) or connection not in state['connections']:
            raise PermissionError('Conexão desconhecida.')
        return state['connections'][connection]

    def change(self, connection, revision, action, value):
        with self._lock, self.lock_factory():
            state = self._read()
            if state['revision'] != revision:
                raise PermissionError('Seleção antiga; atualize a central.')
            row = self._row(state, connection)
            if action == 'default' and value is True:
                state['defaults'][row['operation']] = connection
            elif action == 'label' and isinstance(value, str):
                row['label'] = value
            elif action == 'disconnect' and value is True:
                # Block persistently BEFORE removing the local secret; a crash
                # cannot leave cached runtime credentials operational.
                row['grant'], row['resources'] = '', {}
                row['epoch'] = uuid4().hex
                self._save(state)
                self.auth_factory(row['operation'], self._credential(row)).disconnect_local()
                self._verified.pop(connection, None)
                return self.inventory_unlocked()
            else:
                raise ValueError('Alteração de conta inválida.')
            row['epoch'] = uuid4().hex
            self._save(state)
        return self.inventory()

    def inventory_unlocked(self):
        # A mutation reply without nested acquisition of the interprocess lock.
        return {'changed': True, 'restart_required': True}

    def add(self, operation, label, revision):
        if operation not in {'calendar_read', 'calendar_write', 'drive_docs_read'}:
            raise PermissionError('Operação sem conexão nova suportada.')
        with self._lock, self.lock_factory():
            state = self._read()
            if state['revision'] != revision:
                raise PermissionError('Seleção antiga.')
            key = uuid4().hex
            state['connections'][key] = dict(operation=operation, label=label, credential=f'account-{key}.bin',
                legacy=False, namespace='', subject='', grant='', resources={}, epoch=uuid4().hex)
            self._save(state)
        return {'connection': key, **self.inventory()}

    def test(self, connection, revision):
        """Explicit identity GET; refresh uses the existing OAuth lifecycle."""
        with self._lock:
            state = self._read()
            if state['revision'] != revision:
                raise PermissionError('Seleção antiga.')
            row = self._row(state, connection)
            auth = self.auth_factory(row['operation'], self._credential(row))
            try:
                status = auth.status()
                if status.state is not ConnectionState.CONNECTED:
                    self._verified[connection] = ('expirado' if status.state is ConnectionState.REVOKED else 'erro',
                                                  'Credencial não validada; reconexão explícita necessária.')
                    return self.inventory()
                namespace, subject = self.identity(auth, row['operation'])
                material = self._material(row)
                if material is None or material.revocation_pending:
                    raise PermissionError('Grant ausente.')
                digest = grant_digest(material)
                if ((row['subject'] and (namespace, subject) != (row['namespace'], row['subject']))
                        or (row['grant'] and row['grant'] != digest)):
                    with self.lock_factory():
                        current = self._read()
                        target = self._row(current, connection)
                        target.update(grant='', resources={}, epoch=uuid4().hex)
                        self._save(current)
                    raise PermissionError('Identidade ou grant divergente; não transferir recursos.')
                with self.lock_factory():
                    current = self._read()
                    if current['revision'] != revision:
                        raise PermissionError('Conta alterada durante a consulta.')
                    target = self._row(current, connection)
                    target.update(namespace=namespace, subject=subject, grant=digest)
                    self._save(current)
                self._verified[connection] = ('conectado', 'Identidade verificada por consulta somente leitura; autorização operacional separada.')
            except GoogleTokenRevokedError:
                self._verified[connection] = ('expirado', 'Credencial rejeitada; reconecte explicitamente.')
            except Exception:
                self._verified[connection] = ('erro', 'Identidade/conexão não verificada; detalhes privados omitidos.')
                raise PermissionError('Teste de conexão falhou; nenhuma autoridade adicional concedida.') from None
        return self.inventory()

    def connect(self, connection, revision, client_config):
        with self._lock:
            state = self._read()
            if state['revision'] != revision:
                raise PermissionError('Seleção antiga.')
            row = self._row(state, connection)
            if row['operation'] not in {'calendar_read', 'calendar_write', 'drive_docs_read'}:
                raise PermissionError('Fluxo legado preservado; não ampliar por esta central.')
            auth = self.auth_factory(row['operation'], self._credential(row))
            status = auth.status()
            if status.state is ConnectionState.ERROR:
                raise PermissionError('Credencial em erro; teste ou desconecte localmente antes de consentir novamente.')
            if status.state is not ConnectionState.CONNECTED:
                with self.lock_factory():
                    current = self._read()
                    if current['revision'] != revision:
                        raise PermissionError('Seleção antiga.')
                    target = self._row(current, connection)
                    target.update(resources={}, grant='', epoch=uuid4().hex)
                    self._save(current)
                    revision = current['revision']
            # Existing valid connection is tested, not overwritten silently.
            if self._cancel.is_set():
                raise PermissionError('Runtime fechando; nenhum OAuth iniciado.')
            auth.connect(Path(client_config), cancel=self._cancel)
        return self.test(connection, revision)

    def cancel_connection(self):
        self._cancel.set()

    def default(self, operation):
        self.initialize()
        state = self._read()
        key = state['defaults'][operation]
        return key, dict(self._row(state, key))

    def invalidate(self, operation):
        with self._lock, self.lock_factory():
            state = self._read()
            row = self._row(state, state['defaults'][operation])
            row['epoch'] = uuid4().hex
            self._save(state)

    def auth(self, operation):
        key, row = self.default(operation)
        return BoundAccountAuth(self, key, row, self.auth_factory(operation, self._credential(row)))

    def binding(self, key, snapshot, *, require_default=True):
        state = self._read()
        row = self._row(state, key)
        if ((require_default and state['defaults'][row['operation']] != key) or row != snapshot):
            raise PermissionError('Conta/configuração alterada; reinicie e prepare novamente.')
        material = self._material(row)
        if material is None or material.revocation_pending or not row['grant'] or grant_digest(material) != row['grant']:
            raise PermissionError('Credencial removida/alterada; operação bloqueada.')
        if row['operation'] == 'calendar_write' and (row['namespace'] != 'google.sub' or not row['subject']):
            raise PermissionError('Verifique explicitamente a identidade WRITE antes de preparar.')
        return hashlib.sha256((key + row['epoch'] + row['grant'] + row['subject']).encode()).hexdigest()

    def calendars(self, connection, revision, *, calendar_allowed=None):
        state = self._read()
        if state['revision'] != revision:
            raise PermissionError('Seleção antiga.')
        row = self._row(state, connection)
        if row['operation'] != 'calendar_read' or self._verified.get(connection, ('',))[0] != 'conectado':
            raise PermissionError('Teste a conta Calendar READ primeiro.')
        auth = BoundAccountAuth(self, connection, row,
            self.auth_factory(row['operation'], self._credential(row)), require_default=False)
        client = self._calendar_client(auth)
        material = self._material(row)
        scopes = set(material.scopes) if material else set()
        prefix = 'https://www.googleapis.com/auth/calendar'
        if not scopes.intersection({prefix, prefix + '.readonly', prefix + '.calendarlist', prefix + '.calendarlist.readonly'}):
            if not scopes.intersection({prefix, prefix + '.readonly', prefix + '.calendars', prefix + '.calendars.readonly'}):
                raise PermissionError('A conexão não permite consultar nomes de agendas; revise o consentimento em Avançado.')
            # Minimal READ already authorizes specific resources, not CalendarList.
            # Do not ask for broader consent just to display their real names.
            result = []
            for calendar in dict.fromkeys(row['resources'].values()):
                if calendar_allowed is not None and not calendar_allowed(calendar):
                    continue
                item = client.get('/calendars/' + quote(calendar, safe=''), {})
                if not isinstance(item.get('id'), str) or not isinstance(item.get('summary'), str):
                    raise ValueError('Dados da agenda inválidos.')
                if calendar != 'primary' and item['id'] != calendar:
                    raise PermissionError('Identidade da agenda mudou; nenhuma seleção transferida.')
                result.append({'id': item['id'], 'label': item['summary'], 'role': 'unknown',
                               'timezone': item.get('timeZone', '')})
            return {'revision': revision, 'calendars': result,
                    'notice': 'Agendas já autorizadas; o acesso atual não inclui listar todas as agendas.'}
        data = client.get('/users/me/calendarList', {'maxResults': 100})
        if data.get('nextPageToken'):
            raise PermissionError('Lista excede limite; seleção não concluída.')
        items = data.get('items')
        if not isinstance(items, list):
            raise ValueError('Resposta Calendar inválida.')
        result = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get('id'), str):
                result.append({'id': item['id'], 'label': str(item.get('summary', 'Calendário')),
                               'role': str(item.get('accessRole', 'unknown')),
                               'timezone': str(item.get('timeZone', ''))})
        return {'revision': revision, 'calendars': result}

    def primary_calendar(self, connection, revision, *, calendar_allowed=None):
        """Explicit setup metadata only; does not select resources or enable reads."""
        state = self._read()
        row = self._row(state, connection)
        if (state['revision'] != revision or row['operation'] != 'calendar_read'
                or self._verified.get(connection, ('',))[0] != 'conectado'
                or calendar_allowed is None or not calendar_allowed('primary')):
            raise PermissionError('Conecte a leitura e confira as permissões antes de consultar.')
        auth = BoundAccountAuth(self, connection, dict(row),
            self.auth_factory(row['operation'], self._credential(row)), require_default=False)
        item = self._calendar_client(auth).get('/calendars/primary', {})
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not item['id'] or len(item['id']) > 256
                or any(ord(c) < 32 for c in item['id'])
                or not isinstance(item.get('summary'), str)
                or not isinstance(item.get('timeZone'), str)):
            raise ValueError('Dados da agenda principal inválidos.')
        if not calendar_allowed(item['id']) or self._read()['revision'] != revision:
            raise PermissionError('Permissão ou seleção mudou; nenhuma agenda selecionada.')
        auth.binding()
        return {'revision': revision, 'calendars': [
            {'id': item['id'], 'label': item['summary'], 'role': 'unknown',
             'timezone': item['timeZone'], 'write_candidate': False}],
            'notice': 'Escolha e salve a agenda. A consulta não ativa leitura nem escrita.'}

    def guided_calendar(self, *, calendar_allowed=None):
        """Read-only readiness using existing defaults, never OAuth or a new grant."""
        self.initialize()
        for operation in ('calendar_read', 'calendar_write'):
            state = self._read()
            key = state['defaults'][operation]
            row = self._row(state, key)
            if self._material(row) is not None:
                try:
                    self.test(key, state['revision'])
                except PermissionError:
                    pass  # Inventory carries the sanitized status; no reconnect loop.
        data = self.inventory()
        reader = next(r for r in data['rows'] if r['operation'] == 'calendar_read' and r['default'])
        writer = next(r for r in data['rows'] if r['operation'] == 'calendar_write' and r['default'])
        data['candidate'] = reader['id']
        data['matching_writer'] = (writer['id'] if reader['account_id'] and
            reader['account_id'] == writer['account_id'] and writer['status'] == 'conectado' else None)
        data['calendars'] = []
        data['calendar_state'] = 'connection_required'
        data['calendar_notice'] = 'O acesso de leitura ainda não está válido. Use Conectar/reconectar para a leitura da agenda.'
        _, read_row = self.default('calendar_read')
        read_material = self._material(read_row)
        prefix = 'https://www.googleapis.com/auth/calendar'
        metadata_scopes = {prefix, prefix + '.readonly', prefix + '.calendars', prefix + '.calendars.readonly',
                           prefix + '.calendarlist', prefix + '.calendarlist.readonly'}
        if read_material and not set(read_material.scopes).intersection(metadata_scopes):
            data['calendar_state'] = 'consent_required'
            data['calendar_notice'] = 'A conta está conectada, mas falta consentimento para consultar nomes de agendas. '
            data['calendar_notice'] += 'Use Conectar/reconectar para autorizar a leitura; nenhuma permissão será ampliada automaticamente.'
        if reader['status'] == 'conectado':
            try:
                choices = self.calendars(reader['id'], data['revision'], calendar_allowed=calendar_allowed)
                data['calendars'] = choices['calendars']
                data['calendar_notice'] = choices.get('notice', 'Escolha uma agenda pelo nome real.')
                data['calendar_state'] = 'ready' if data['calendars'] else 'empty'
                if not data['calendars']:
                    data['calendar_notice'] = 'Nenhuma agenda disponível dentro da autorização atual. '
                    data['calendar_notice'] += ('Confira os bloqueios em Permissões.' if read_row['resources'] else
                        'Nenhuma agenda foi vinculada ao acesso de leitura. A conta conectada, sozinha, não autoriza uma agenda.')
            except GooglePermissionError:
                data['calendar_state'] = 'access_denied'
                data['calendar_notice'] = 'O Google não permitiu consultar a agenda. Verifique se Calendar API está habilitada e se o consentimento de leitura é válido. Não faremos outro login automaticamente.'
            except GoogleResourceNotFoundError:
                data['calendar_state'] = 'resource_missing'
                data['calendar_notice'] = 'A agenda autorizada não foi encontrada. Confira a agenda vinculada em Avançado; atualizar não escolhe outra agenda.'
            except (GoogleRateLimitError, GoogleNetworkError):
                data['calendar_state'] = 'unavailable'
                data['calendar_notice'] = 'O Google está temporariamente indisponível. Tente Atualizar conta e agendas mais tarde; não é necessário iniciar outro login agora.'
            except (PermissionError, ValueError, RuntimeError):
                if data['calendar_state'] != 'consent_required':
                    data['calendar_state'] = 'error'
                    data['calendar_notice'] = 'Não foi possível carregar agendas. Confira acesso e conexão em Avançado e tente atualizar.'
        for item in data['calendars']:
            item['write_candidate'] = bool(data['matching_writer'] and item['role'] in {'owner', 'unknown'})
            if item['write_candidate']:
                try:
                    from jarvis.integrations.google.calendar.google_provider import GoogleCalendarWriteProvider
                    auth = self.auth('calendar_write')
                    GoogleCalendarWriteProvider(self._calendar_client(auth)).private_calendar_version(item['id'])
                except (PermissionError, RuntimeError, ValueError):
                    item['write_candidate'] = False
        return data

    def select_calendar(self, read_connection, write_connection, revision, calendar, alias, *, calendar_allowed=None):
        """Explicit resource choice; NEVER enables WRITE or copies READ authority."""
        choices = self.calendars(read_connection, revision, calendar_allowed=calendar_allowed)['calendars']
        if not choices:
            # Reconnection invalidates resource grants. A minimal READ grant
            # cannot enumerate CalendarList to select a resource again. Only
            # an explicit, policy-checked administrative choice can bootstrap
            # metadata discovery; it never restores old grants automatically.
            state = self._read()
            reader = self._row(state, read_connection)
            if (state['revision'] != revision or reader['resources']
                    or not isinstance(calendar, str) or not calendar or len(calendar) > 256
                    or any(ord(char) < 32 for char in calendar)
                    or calendar_allowed is None or not calendar_allowed(calendar)):
                raise PermissionError('Seleção explícita indisponível ou bloqueada.')
            auth = BoundAccountAuth(self, read_connection, dict(reader),
                self.auth_factory(reader['operation'], self._credential(reader)), require_default=False)
            item = self._calendar_client(auth).get('/calendars/' + quote(calendar, safe=''), {})
            if (not isinstance(item, dict) or item.get('id') != calendar
                    or not isinstance(item.get('summary'), str)):
                raise PermissionError('Identidade da agenda não confirmada; nenhuma seleção salva.')
            choices = [{'id': calendar, 'label': item['summary'], 'role': 'unknown',
                        'timezone': item.get('timeZone', '')}]
        if not any(item['id'] == calendar for item in choices):
            raise PermissionError('Selecione um calendário retornado pela API.')
        with self._lock:
            state = self._read()
            if state['revision'] != revision:
                raise PermissionError('Seleção antiga.')
            reader = self._row(state, read_connection)
            target = self._row(state, write_connection or read_connection)
            if write_connection:
                if (target['operation'] != 'calendar_write' or not reader['subject']
                        or (target['namespace'], target['subject']) != (reader['namespace'], reader['subject'])
                        or self._verified.get(write_connection, ('',))[0] != 'conectado'
                        or not any(item['id'] == calendar and item['role'] in {'owner', 'unknown'} for item in choices)):
                    raise PermissionError('WRITE requer grant mínimo da mesma identidade e calendário próprio.')
                from jarvis.integrations.google.calendar.google_provider import GoogleCalendarWriteProvider
                auth = BoundAccountAuth(self, write_connection, dict(target),
                    self.auth_factory(target['operation'], self._credential(target)), require_default=False)
                GoogleCalendarWriteProvider(self._calendar_client(auth)).private_calendar_version(calendar)
            # The authenticated request above owns its cross-process guard.
            # Acquire a fresh mutation lock only after it releases that guard,
            # and reject changes made while the ACL was being consulted.
            with self.lock_factory():
                current = self._read()
                if current['revision'] != revision:
                    raise PermissionError('Seleção alterada durante a consulta; revise novamente.')
                target = self._row(current, write_connection or read_connection)
                target['resources'] = {alias: calendar}
                target['epoch'] = uuid4().hex
                self._save(current)
        return self.inventory()


class BoundAccountAuth:
    """No fallback or authority migration; cached tokens cannot bypass removal."""
    def __init__(self, service, key, snapshot, auth, *, require_default=True):
        self.service, self.key, self.snapshot, self.auth = service, key, snapshot, auth
        self.require_default = require_default

    def binding(self):
        return self.service.binding(self.key, self.snapshot, require_default=self.require_default)

    @contextmanager
    def request_guard(self):
        self.binding()
        with self.service._lock, self.service.lock_factory():
            self.binding()
            yield
            self.binding()

    def credentials(self):
        self.binding()
        handle = self.auth.credentials()
        if grant_digest(handle.storage_material()) != self.snapshot['grant']:
            raise PermissionError('Grant mudou durante obtenção da credencial.')
        self.binding()
        return handle

    def refresh_credentials(self):
        self.binding()
        handle = self.auth.refresh_credentials()
        if grant_digest(handle.storage_material()) != self.snapshot['grant']:
            raise PermissionError('Grant mudou durante refresh.')
        self.binding()
        return handle


class LegacyBoundAccountAuth(BoundAccountAuth):
    """Defer local migration until access; preserve composition without I/O."""
    def __init__(self, service, operation, auth):
        self.service, self.operation, self.auth = service, operation, auth
        self.key = hashlib.sha256(('legacy:' + operation).encode()).hexdigest()[:32]
        self.snapshot = None
        self.require_default = True

    def binding(self):
        if self.snapshot is None:
            key, row = self.service.default(self.operation)
            if key != self.key:
                raise PermissionError('Padrão alterado; reinicie o runtime.')
            self.snapshot = row
        return super().binding()
