"""Explicit desktop authorization workflow. No browser automation or document cache."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from hmac import compare_digest
import json
from pathlib import Path
import re
import secrets
from threading import Event, RLock
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from jarvis.config import JarvisConfig
from jarvis.integrations.google.client import GoogleOAuthFlowClient, load_client_config
from jarvis.integrations.google.drive_docs.client import DriveDocsClient, grant_digest
from jarvis.integrations.google.drive_docs.picker_callback import PickerOAuthReceiver
from jarvis.integrations.google.drive_docs.service import DOC
from jarvis.integrations.google.errors import GoogleIntegrationError, GoogleScopeError, GoogleTokenRevokedError
from jarvis.integrations.google.factory import google_credential_dir
from jarvis.integrations.google.operation_lock import GoogleOperationLock
from jarvis.integrations.google.scopes import DRIVE_PICKER_SCOPES
from jarvis.integrations.google.storage import ProtectedFileCredentialStore, WindowsDPAPIProtector
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import Tool, ToolPreparation
from jarvis.tools.executor import ToolExecutor


POLICY_KEYS = ('google_drive_read', 'google_docs_read', 'google_drive_docs_files',
               'google_drive_docs_folders', 'google_drive_docs_grant', 'google_drive_docs_account')


class LocalDocumentAuthorizations:
    """Only local settings; unknown settings and Calendar keys remain intact."""
    def __init__(self, config: JarvisConfig) -> None:
        self.config = config
        self.lock = RLock()

    def read(self) -> dict:
        try:
            if not self.config.settings_path.exists():
                return {}
            raw = self.config.settings_path.read_bytes()
            if len(raw) > 1_048_576:
                raise ValueError()
            value = json.loads(raw.decode('utf-8'))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, UnicodeError, ValueError):
            raise PermissionError('Settings indisponível; nenhuma autorização alterada.') from None

    def snapshot(self) -> dict:
        settings = self.read()
        return {key: deepcopy(settings.get(key)) for key in POLICY_KEYS}

    def watch(self):
        initial = self.snapshot()
        def current():
            try:
                return self.snapshot() == initial
            except PermissionError:
                return False
        return current

    def add(self, ids: tuple[str, ...], grant: str, account: str, before: dict) -> None:
        with self.lock:
            settings = self.read()
            if self.snapshot() != before:
                raise PermissionError('Autorizações mudaram durante a seleção; tente nova ação explícita.')
            files = dict(settings.get('google_drive_docs_files', {}))
            if before.get('google_drive_docs_account') not in (None, '', account):
                raise PermissionError('Conta diferente; desconecte localmente antes de trocar de conta.')
            for file_id in ids:
                if file_id not in files.values():
                    index = 1
                    while f'documento_{index}' in files:
                        index += 1
                    files[f'documento_{index}'] = file_id
            if len(files) > 20:
                raise PermissionError('Limite de vinte arquivos; remova autorizações antes de selecionar.')
            settings.update(google_drive_docs_files=files, google_drive_docs_grant=grant,
                            google_drive_docs_account=account, google_drive_read=False,
                            google_docs_read=False)
            self.config._write_local_settings(settings)

    def remove(self, alias: str) -> None:
        with GoogleOperationLock(), self.lock:
            settings = self.read()
            files = dict(settings.get('google_drive_docs_files', {}))
            if alias not in files:
                raise PermissionError('Alias não autorizado.')
            del files[alias]
            settings.update(google_drive_docs_files=files, google_drive_read=False, google_docs_read=False)
            self.config._write_local_settings(settings)

    def disconnect_local(self, store) -> None:
        with self.lock:
            settings = self.read()
            settings.update(google_drive_docs_files={}, google_drive_docs_folders={},
                            google_drive_docs_grant='', google_drive_docs_account='',
                            google_drive_read=False, google_docs_read=False)
            self.config._write_local_settings(settings)  # fail closed before forgetting refresh material
            store.delete()  # NOT OAuth revoke; never touches Calendar's store


class NativePickerFlow(GoogleOAuthFlowClient):
    token_exchange_timeout = 10.

    def authorization_url(self) -> tuple[str, str]:
        try:
            url, state = self._flow.authorization_url(
                access_type='offline', prompt='consent', include_granted_scopes='false',
                trigger_onepick='true', allow_multiple='true', mimetypes=DOC,
                allow_folder_selection='false')
            return str(url), str(state)
        except Exception:
            raise GoogleIntegrationError('Não foi possível preparar o Picker desktop.') from None


class _SessionAuth:
    def __init__(self, handle):
        self.handle = handle
    def credentials(self):
        return self.handle
    def refresh_credentials(self):
        # A freshly exchanged Picker token must work. Do not extend a revoked session.
        raise GoogleTokenRevokedError('Grant Picker rejeitado; nova seleção explícita necessária.')


class GooglePickerValidateTool(Tool):
    """UI-only sealed session, never registered in an LLM/Harness tool registry."""
    name = 'google_drive_picker_validate'
    description = 'Revalida seleção explícita desktop e grava somente autorização local.'
    risk = RiskLevel.REVERSIBLE_LOCAL
    parameters = {'type': 'object', 'properties': {'interaction': {
        'type': 'string', 'pattern': '^[a-f0-9]{32}$'}},
        'required': ['interaction'], 'additionalProperties': False}

    def __init__(self, interaction, ids, handle, client, local, store, before,
                 previous, deadline, cancel):
        self.interaction, self.ids, self.handle, self.client = interaction, tuple(ids), handle, client
        self.local, self.store, self.before, self.previous = local, store, before, previous
        self.deadline, self.cancel = deadline, cancel
        self._used = False
        self._lock = RLock()

    def get_target(self, **kwargs) -> str:
        value = (self.interaction, self.ids, grant_digest(self.handle.storage_material()), kwargs)
        return 'google_picker:' + hashlib.sha256(repr(value).encode()).hexdigest()

    def preflight(self, **kwargs) -> None:
        if (set(kwargs) != {'interaction'} or kwargs['interaction'] != self.interaction
                or self._used or time.monotonic() >= self.deadline or self.cancel.is_set()
                or not 1 <= len(self.ids) <= 20 or len(set(self.ids)) != len(self.ids)
                or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', item) for item in self.ids)):
            raise PermissionError('Sessão Picker inválida, expirada ou já utilizada.')
        material = self.handle.storage_material()
        if (set(material.scopes) != set(DRIVE_PICKER_SCOPES) or material.revocation_pending
                or (self.previous is not None and self.previous.client_id != material.client_id)):
            raise PermissionError('Grant/client diferente; nenhuma autorização local.')

    def execute_prepared(self, preparation: ToolPreparation, **kwargs) -> str:
        with self._lock:
            if preparation.target != self.get_target(**kwargs):
                raise PermissionError('Seleção alterada após preparação.')
            return self.execute(**kwargs)

    def execute(self, **kwargs) -> str:
        with self._lock:
            self.preflight(**kwargs)
            self._used = True
            try:
                deadline = min(self.deadline, time.monotonic() + 10.)
                user = self.client.get('drive', '/about', {'fields': 'user(permissionId)'}, deadline=deadline)
                permission = user.get('user', {}).get('permissionId')
                if not isinstance(permission, str) or not permission or len(permission) > 256:
                    raise PermissionError('Identidade Google não confirmada.')
                account = hashlib.sha256(permission.encode()).hexdigest()
                if self.before.get('google_drive_docs_account') not in (None, '', account):
                    raise PermissionError('Conta Google divergente.')
                for file_id in self.ids:
                    if time.monotonic() >= deadline or self.cancel.is_set():
                        raise PermissionError('Seleção expirada/cancelada.')
                    item = self.client.get('drive', '/files/' + file_id,
                        {'fields': 'id,mimeType,trashed,isAppAuthorized', 'supportsAllDrives': 'true'},
                        deadline=deadline)
                    if (item.get('id') != file_id or item.get('mimeType') != DOC
                            or item.get('trashed') is not False or item.get('isAppAuthorized') is not True):
                        raise PermissionError('Arquivo/tipo/concessão Google não confirmado.')
                if time.monotonic() >= deadline or self.cancel.is_set():
                    raise PermissionError('Seleção expirada/cancelada.')
                if self.local.snapshot() != self.before:
                    raise PermissionError('Autorizações alteradas durante o Picker.')
                material = self.handle.storage_material()
                self.store.save(material)
                try:
                    self.local.add(self.ids, grant_digest(material), account, self.before)
                except Exception:
                    if self.previous is None:
                        self.store.delete()
                    else:
                        self.store.save(self.previous)
                    raise
                return f'{len(self.ids)} documento(s) autorizado(s) localmente; READ permanece desligado. Reinicie após os gates reais.'
            except Exception:
                raise RuntimeError('Seleção Google não validada; nenhum documento autorizado por esta operação.') from None


class GoogleDocumentPicker:
    def __init__(self, config: JarvisConfig, *, store=None, flow_factory=NativePickerFlow,
                 receiver_factory=PickerOAuthReceiver, browser_open=webbrowser.open,
                 opener=None, executor=None):
        self.config = config
        self._managed_account_store = store is None
        self.local = LocalDocumentAuthorizations(config)
        self.store = store or ProtectedFileCredentialStore(
            google_credential_dir() / 'credentials_drive_docs_read.bin', WindowsDPAPIProtector())
        self.flow_factory, self.receiver_factory, self.browser_open = flow_factory, receiver_factory, browser_open
        self.opener = opener
        from jarvis.security.permissions import PermissionStore
        self.permissions = PermissionStore(config.settings_path.parent / 'permissions.json',
            tuple(p for p in (config.filesystem_base_directory, config.obsidian_vault_path) if p is not None))
        self.executor = executor or ToolExecutor(SafetyManager(), AuditLogger(config.audit_log_path),
            CapabilityPolicy({Capability.GOOGLE_DRIVE_SELECTION: True}), permissions=self.permissions)

    def select(self, client_config_path: Path, *, timeout_seconds: float = 120.,
               cancel: Event | None = None) -> str:
        if self._managed_account_store:
            from jarvis.integrations.google.accounts import require_legacy_account
            require_legacy_account('drive_docs_read')
        self.permissions.require('tool:google_drive_picker_validate')
        if not 0 < timeout_seconds <= 120:
            raise ValueError('Prazo Picker deve estar entre zero e120s.')
        client_config = load_client_config(client_config_path)['installed']
        cancel = cancel or Event()
        with GoogleOperationLock():
            before = self.local.snapshot()
            previous = self.store.load()
            if (previous is not None and before.get('google_drive_docs_files')
                    and not before.get('google_drive_docs_account')):
                raise PermissionError('Allowlist legada sem identidade Google; revise e desconecte localmente antes do Picker.')
            if previous is not None and (previous.revocation_pending
                    or previous.client_id != client_config['client_id']):
                raise PermissionError('Grant/client mudou; desconecte localmente antes de trocar.')
            nonce, interaction = secrets.token_urlsafe(32), secrets.token_hex(16)
            deadline = time.monotonic() + timeout_seconds
            with self.receiver_factory() as receiver:
                receiver.arm(nonce, timeout_seconds)
                flow = self.flow_factory(client_config_path, DRIVE_PICKER_SCOPES, nonce, receiver.redirect_uri)
                url, state = flow.authorization_url()
                parts = urlsplit(url)
                params = parse_qs(parts.query)
                if (not compare_digest(nonce, state) or parts.scheme != 'https'
                        or parts.netloc != 'accounts.google.com' or parts.fragment
                        or params.get('state') != [nonce]
                        or params.get('scope') != [DRIVE_PICKER_SCOPES[0]]
                        or params.get('trigger_onepick') != ['true']
                        or params.get('redirect_uri') != [receiver.redirect_uri]
                        or params.get('client_id') != [client_config['client_id']]
                        or params.get('response_type') != ['code']
                        or params.get('include_granted_scopes') != ['false']
                        or params.get('prompt') != ['consent']
                        or params.get('mimetypes') != [DOC]
                        or params.get('allow_folder_selection') != ['false']
                        or params.get('code_challenge_method') != ['S256']
                        or not params.get('code_challenge')):
                    raise PermissionError('URL Picker não corresponde à sessão preparada.')
                try:
                    opened = self.browser_open(url)
                except Exception:
                    opened = False
                if not opened:
                    raise RuntimeError('Navegador não abriu; nenhuma seleção iniciada.')
                callback = receiver.wait(cancel)
                if time.monotonic() >= deadline or cancel.is_set():
                    raise PermissionError('Sessão Picker expirada/cancelada antes da troca OAuth.')
                if callback.oauth.state is None or not compare_digest(callback.oauth.state, nonce):
                    raise PermissionError('Nonce Picker divergente.')
                handle = flow.exchange(callback.oauth.authorization_response)
            if set(handle.scopes) != set(DRIVE_PICKER_SCOPES):
                raise GoogleScopeError('Picker retornou scopes diferentes; Calendar não será alterado.')
            if handle.storage_material().client_id != client_config['client_id']:
                raise PermissionError('Client OAuth divergente.')
            auth = _SessionAuth(handle)
            arguments = {} if self.opener is None else {'opener': self.opener}
            client = DriveDocsClient(auth, lambda: grant_digest(handle.storage_material()), **arguments)
            tool = GooglePickerValidateTool(interaction, callback.file_ids, handle, client,
                self.local, self.store, before, previous, deadline, cancel)
            result = self.executor.execute_with_result(tool, interaction=interaction)
            if result.status != 'success':
                raise RuntimeError('Seleção Google bloqueada/não validada; flags permanecem desligadas.')
            return result.content

    def disconnect_local(self) -> None:
        if self._managed_account_store:
            from jarvis.integrations.google.accounts import require_legacy_account
            require_legacy_account('drive_docs_read')
        with GoogleOperationLock():
            self.local.disconnect_local(self.store)
