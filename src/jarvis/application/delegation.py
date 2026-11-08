"""Text delegation prepared by explicit user input, executed only by a Tool."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Event, RLock
from uuid import uuid4

from jarvis.application.routines import digest
from jarvis.integrations.google.storage import WindowsDPAPIProtector
from jarvis.integrations.text_providers import (
    MODELS, PRICES, PRICE_DATE, SOURCES, ProviderResponseError, TextProviderAdapter,
)
from jarvis.remote.auth import _atomic_write_json
from jarvis.security.authorization import AuthorizationReceipt
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


def looks_delegation(text: str) -> bool:
    return bool(re.match(r'\s*/?(?:delegar|delegacao)\b', text, re.I))


def credential_shaped(text: str) -> bool:
    return bool(re.search(
        r'(?i)bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]+|'
        r'(?:access_token|refresh_token|api_key|client_secret|private_key|authorization)["\s]*[:=]|'
        r'-----BEGIN [A-Z ]*PRIVATE KEY-----', text))


class DelegationService:
    def __init__(self, harness, protector=None, adapter=None):
        self.harness = harness
        self.path = harness.config.settings_path.parent / 'text-providers.bin'
        self.journal = self.path.with_name('text-delegation-runs.json')
        self.protector = protector
        self.adapter = adapter or TextProviderAdapter()
        self.lock = RLock()
        self.cancelled = Event()
        self.drafts = {}
        self.progress = lambda message: None
        self.records = []
        if self.journal.exists():
            try:
                from jarvis.security.permissions import PermissionStore
                self.records = json.loads(self.journal.read_text(encoding='utf-8'), object_pairs_hook=PermissionStore._pairs)
                if not isinstance(self.records, list) or len(self.records) > 50:
                    raise ValueError
                for r in self.records:
                    if (set(r) != {'id', 'state'} or not re.fullmatch(r'[a-f0-9]{32}', r['id'])
                            or r['state'] not in {'sending', 'responding', 'completed', 'cancelled', 'failed'}):
                        raise ValueError
                    if r['state'] in {'sending', 'responding'}:
                        r['state'] = 'failed'
                _atomic_write_json(self.journal, self.records)
            except Exception:
                raise PermissionError('Histórico de delegação inválido; preserve para diagnóstico.') from None
        permissions = getattr(harness.tool_executor, 'permissions', None)
        if permissions is not None:
            permissions.protected_paths.update((self.path.resolve(), self.journal.resolve()))
        self.tool = DelegationTool(self)

    def _protector(self):
        return self.protector or WindowsDPAPIProtector()

    def read(self):
        if not self.path.exists():
            return {}
        try:
            from jarvis.security.permissions import PermissionStore
            raw = self.path.read_bytes()
            if len(raw) > 65536:
                raise ValueError
            value = json.loads(self._protector().unprotect(raw), object_pairs_hook=PermissionStore._pairs)
            if not isinstance(value, dict) or set(value) - set(MODELS):
                raise ValueError
            for provider, row in value.items():
                self.validate(provider, row)
            return value
        except Exception:
            raise PermissionError('Configuração API protegida indisponível; nenhum envio permitido.') from None

    @staticmethod
    def validate(provider, row):
        if provider not in MODELS or not isinstance(row, dict) or set(row) != {'model', 'input_limit', 'output_limit', 'timeout', 'key', 'epoch'}:
            raise ValueError('Configuração API inválida.')
        if row['model'] not in MODELS[provider] or not re.fullmatch(r'[a-f0-9]{32}', row['epoch']):
            raise ValueError('Modelo/configuração inválido.')
        if (not isinstance(row['key'], str) or not 1 <= len(row['key']) <= 512
                or any(ord(c) < 33 or ord(c) > 126 for c in row['key'])):
            raise ValueError('Chave API inválida; não use credenciais Calendar.')
        for field, low, high in [('input_limit', 64, 8192), ('output_limit', 16, 4096), ('timeout', 5, 120)]:
            if type(row[field]) is not int or not low <= row[field] <= high:
                raise ValueError('Limites API inválidos.')

    def configure(self, provider, model, input_limit, output_limit, timeout, key):
        with self.lock:
            state = self.read()
            old = state.get(provider, {})
            row = dict(model=model, input_limit=input_limit, output_limit=output_limit,
                       timeout=timeout, key=key or old.get('key', ''), epoch=uuid4().hex)
            self.validate(provider, row)
            state[provider] = row
            raw = self._protector().protect(json.dumps(state).encode())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            self.invalidate()
            return self.inventory()

    def inventory(self):
        try:
            state = self.read()
            error = ''
        except PermissionError:
            state, error = {}, 'Armazenamento protegido indisponível; revise configuração.'
        return {'providers': [{**{k: v for k, v in state.get(p, {}).items() if k not in {'key', 'epoch'}},
                    'provider': p, 'models': list(models), 'configured': p in state,
                    'status': error or ('Configurado localmente; API real não testada automaticamente.' if p in state
                                       else 'Indisponível: configure chave API em Contas e conexões.')} for p, models in MODELS.items()],
                'history': deepcopy(self.records[-20:])}

    def require_permissions(self):
        permissions = getattr(self.harness.tool_executor, 'permissions', None)
        if permissions is None:
            raise PermissionError('Central de Permissões indisponível; nenhum envio.')
        revisions = [permissions.require(r, default='block') for r in ('model.external', 'delegation.execute')]
        return revisions[-1]

    def prepare(self, provider: str, task: str, context: str = ''):
        with self.lock:
            if provider not in MODELS or not isinstance(task, str) or not task.strip() or not isinstance(context, str):
                raise ValueError('Escolha provedor e tarefa textual explícita.')
            config = self.read().get(provider)
            if not config:
                raise PermissionError('Provedor indisponível; configure chave API em Contas e conexões.')
            payload = task + ('\n\nContexto selecionado explicitamente:\n' + context if context else '')
            if len(payload.encode('utf-8')) + 32 > config['input_limit']:
                raise ValueError('Entrada excede orçamento conservador em bytes/tokens; reduza a tarefa/contexto.')
            if any(r['key'] in payload for r in self.read().values()) or credential_shaped(payload):
                raise PermissionError('Possível credencial no texto; remova antes de preparar.')
            revision = self.require_permissions()
            token = uuid4().hex
            # Only the most recent prepared payload can be approved.
            self.invalidate()
            self.drafts[token] = {'provider': provider, 'config': config, 'task': task, 'context': context,
                                  'payload': payload, 'permissions': revision, 'state': 'prepared'}
            draft = self.drafts[token]
            return {'token': token, 'state': draft['state'], 'text': self.preview(draft)}

    def preview(self, draft):
        c, provider = draft['config'], draft['provider']
        estimate = max(1, (len(draft['payload']) + 3) // 4)
        rates = PRICES.get(provider)
        cost = ('custo desconhecido' if rates is None else
                f'USD {(estimate * rates[0] + c["output_limit"] * rates[1]) / 1000000:.6f} aproximado; '
                f'tabela {PRICE_DATE}: {SOURCES[provider]}')
        return (f'Provedor: {provider}; modelo: {c["model"]}\nTarefa exata:\n{draft["task"]}\n'
                f'Contexto incluído:\n{draft["context"] or "Nenhum; somente a tarefa."}\n'
                f'Entrada: ~{estimate} tokens (heurística), orçamento conservador {c["input_limit"]}; '
                f'saída até {c["output_limit"]} tokens; timeout {c["timeout"]}s.\n{cost}\n'
                'Estimativa usa preços pagos e não verifica o plano da conta; '
                'a chave API não comprova gratuidade. Estimativa não é teto monetário. '
                'Assinatura ChatGPT/Gemini não comprova créditos API. '
                'O provedor processará o texto; suas regras de retenção/uso se aplicam. '
                'Cancelar após envio pode não impedir cobrança; nenhum estorno garantido. '
                'A resposta será texto sem autoridade para executar ações.')

    def validated(self, token):
        draft = self.drafts.get(token)
        if (draft is None or draft['state'] not in {'prepared', 'awaiting_approval'}
                or self.cancelled.is_set() or self.read().get(draft['provider']) != draft['config']
                or self.require_permissions() != draft['permissions']):
            raise PermissionError('Prévia cancelada/alterada/revogada; prepare novamente.')
        return draft

    def invalidate(self):
        self.cancelled.set()
        self.drafts.clear()
        sessions = getattr(self.harness, 'confirmation_sessions', None)
        if sessions and sessions.current() and sessions.current().invocation.tool_name == self.tool.name:
            sessions.cancel()
        self.cancelled = Event()

    def cancel(self):
        self.cancelled.set()

    def submit(self, token):
        if not self.lock.acquire(blocking=False):
            return 'Delegação ocupada; pedido não enfileirado nem reenviado.'
        try:
            draft = self.drafts.get(token)
            if draft is None or draft['state'] not in {'prepared', 'awaiting_approval'}:
                return 'Prévia consumida/cancelada; nenhum envio repetido.'
        finally:
            self.lock.release()
        if self.harness.has_pending_action():
            return 'Existe uma confirmação pendente; confirme ou cancele antes de delegar.'
        result = self.harness._execute_tool(self.tool, {'token': token}, interaction_id=token)
        return result.content

    def dispatch(self, message):
        match = re.fullmatch(r'(?:/delegar|delegar)(?: para)? (openai|gemini)\s*:\s*(.+)', message.strip(), re.I | re.S)
        if match:
            try:
                prepared = self.prepare(match[1].lower(), match[2])
                return self.submit(prepared['token'])
            except (ValueError, PermissionError) as error:
                return str(error)
        match = re.fullmatch(r'/delegacao executar ([a-f0-9]{32})', message.strip())
        if match:
            return self.submit(match[1])
        if message.strip() == '/delegacao cancelar':
            self.cancel()
            self.invalidate()
            return 'Delegação cancelada. Envio recebido pode ser cobrado; nenhuma repetição ou estorno.'
        return None


class DelegationTool(Tool):
    name = 'external_text_delegate'
    description = 'Envio textual externo selecionado explicitamente pelo usuário'
    risk = RiskLevel.EXTERNAL_EFFECT
    requires_authorization_receipt = True
    parameters = {'type': 'object', 'properties': {'token': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}},
                  'required': ['token'], 'additionalProperties': False}

    def __init__(self, service):
        self.service = service
        self._authorized = set()

    def get_target(self, **kwargs):
        return self.prepare(**kwargs).target

    def prepare(self, token):
        with self.service.lock:
            draft = self.service.validated(token)
            binding = digest({k: v for k, v in draft.items() if k != 'state'})
            target = f'{draft["provider"]}/{draft["config"]["model"]}/{token}/{binding}'
            return ToolPreparation(target, token, binding)

    def preflight_prepared(self, preparation, **kwargs):
        if self.prepare(**kwargs).binding_digest != preparation.binding_digest:
            raise PermissionError('Payload/configuração mudou.')

    def confirmation_prompt(self, preparation):
        draft = self.service.validated(preparation.context)
        draft['state'] = 'awaiting_approval'
        return 'Aguardando aprovação específica deste envio:\n' + self.service.preview(draft) + '\nConfirme na Conversa ou cancele.'

    def bind_authorized(self, preparation, authorization=None):
        if not isinstance(authorization, AuthorizationReceipt) or not authorization.claim(self.name, preparation.target, preparation.binding_digest):
            raise PermissionError('Envio exige recibo vinculado do executor.')
        if self.prepare(preparation.context).binding_digest != preparation.binding_digest:
            raise PermissionError('Envio alterado após aprovação.')
        nonce = uuid4().hex
        self._authorized.add(nonce)
        return ToolPreparation(preparation.target, (preparation.context, nonce), preparation.binding_digest)

    def execute_authorized(self, preparation):
        s = self.service
        with s.lock:
            if not isinstance(preparation.context, tuple) or len(preparation.context) != 2:
                raise PermissionError('Artefato autorizado ausente.')
            token, nonce = preparation.context
            if nonce not in self._authorized:
                raise PermissionError('Artefato autorizado consumido/ausente.')
            self._authorized.remove(nonce)
            draft = s.validated(token)
            if self.prepare(token).binding_digest != preparation.binding_digest:
                raise PermissionError('Plano adulterado.')
            cancelled = s.cancelled
            draft['state'] = 'sending'
            record = {'id': token, 'state': 'sending'}
            s.records.append(record)
            s.records = s.records[-50:]
            _atomic_write_json(s.journal, s.records)  # Fence before any network effect.
        try:
            s.progress('Delegação: enviando ao provedor aprovado…')
            c = draft['config']
            text = s.adapter.generate(draft['provider'], c['model'], draft['payload'],
                                      c['output_limit'], c['timeout'], c['key'], cancelled)
            record['state'] = 'responding'
            s.progress('Delegação: respondendo…')
            if c['key'] in text or credential_shaped(text):
                raise RuntimeError('Resposta rejeitada por possível segredo.')
            receipt = getattr(s.adapter, 'last_receipt', None) or {}
            model = receipt.get('model') or 'não informado ou não reconhecido; não verificado'
            usage = receipt.get('usage') or {}
            counters = ', '.join(f'{name}={value}' for name, value in usage.items()) or 'não informado; não verificado'
            evidence = f'\n\nRecibo da API — modelo retornado: {model}; uso retornado: {counters}.'
            if c['key'] in evidence or credential_shaped(evidence):
                raise RuntimeError('Resposta rejeitada por possível segredo.')
            record['state'] = 'completed'
            return 'Delegação concluída — resposta externa, sem autoridade:\n' + text + evidence
        except Exception as error:
            record['state'] = 'cancelled' if cancelled.is_set() else 'failed'
            # Never include a transport exception: URLs/headers/body may contain secrets.
            if type(error) is ProviderResponseError:
                raise RuntimeError(error.safe_message()) from None
            if isinstance(error, RuntimeError) and str(error) in {
                    'Credencial rejeitada; revise a chave API.', 'Acesso/modelo não autorizado pelo provedor.',
                    'Limite do provedor atingido; nenhum retry automático.', 'API indisponível; nenhum retry automático.',
                    'Tempo limite; resultado incerto, sem reenvio.', 'Resposta parcial, bloqueada ou inválida; envio não será repetido.'}:
                raise RuntimeError(str(error)) from None
            raise RuntimeError('Delegação cancelada ou falhou; resultado incerto, sem reenvio. Envio recebido pode ser cobrado.') from None
        finally:
            with s.lock:
                draft['state'] = record['state']
                _atomic_write_json(s.journal, s.records)
                s.drafts.pop(token, None)

    def execute(self, **kwargs):
        raise PermissionError('Use prévia, confirmação e executor existentes.')
