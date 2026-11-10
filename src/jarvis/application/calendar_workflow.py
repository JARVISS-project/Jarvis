"""Opt-in calendar monitoring and bound, auditable human/rule approvals."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo

from jarvis.application.calendar_assistant import people_for, suggestions, message_draft, MESSAGE_TEMPLATES
from jarvis.application.profile_state import ProtectedProfileState
from jarvis.integrations.contact_messaging import ContactMessagingAdapter, MessageRejectedError
from jarvis.security.authorization import AuthorizationReceipt
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.confirmation_session import digest_arguments
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation
from jarvis.tools.executor import ToolExecutor


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


SETTINGS = {'enabled': False, 'mode': 'prepare', 'alias': 'principal', 'interval_minutes': 5,
            'horizon_hours': 48, 'quiet_start': 22, 'quiet_end': 7,
            'timezone': 'America/Sao_Paulo', 'use_ai': False}
FINAL = {'accepted', 'handed_off', 'result_unknown', 'sending', 'user_reported_sent'}
STATE_LABELS = {'pending': 'Precisa da sua aprovação', 'needs_contact': 'Escolha o contato',
    'snoozed': 'Lembrar depois', 'ignored': 'Ignorado por você', 'review': 'Mudança: revise antes de agir',
    'sending': 'Resultado ainda não confirmado', 'result_unknown': 'Resultado incerto; sem repetição',
    'accepted': 'Aceita pelo servidor de e-mail', 'handed_off': 'Conclua o envio no WhatsApp',
    'rejected': 'Envio recusado', 'unavailable': 'Fora da consulta atual', 'user_reported_sent': 'Envio informado por você'}


class MessageSetupRequiredError(PermissionError):
    pass


class CalendarWorkflow:
    def __init__(self, admin, *, clock=None, adapter=None):
        self.admin = admin
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.adapter = adapter or ContactMessagingAdapter()
        self.store = ProtectedProfileState(admin.config.settings_path.parent / 'calendar-workflow.bin')
        self.channel_store = ProtectedProfileState(admin.config.settings_path.parent / 'message-channels.bin')
        admin.permissions.protected_paths.update((self.store.path.resolve(), self.channel_store.path.resolve()))
        default = {'settings': SETTINGS, 'proposals': {}, 'history': [], 'seen': {}, 'links': {},
                   'rules': [], 'attempts': {}, 'last_scan': None, 'last_error': '', 'revision': uuid4().hex}
        self.data = self.store.read(default)
        if set(self.data) != set(default):
            raise PermissionError('Estado de automação inválido.')
        self.validate_settings(self.data['settings'])
        if (not isinstance(self.data['proposals'], dict) or len(self.data['proposals']) > 60
                or not isinstance(self.data['rules'], list) or len(self.data['rules']) > 20
                or not isinstance(self.data['history'], list) or len(self.data['history']) > 100):
            raise PermissionError('Estado de automação acima dos limites.')
        self.lock = RLock()
        self.tickets = {}
        self._authorized = set()
        self._closed = False
        self.next_scan = self.clock()
        self.executor = None
        self.safety = None
        self.tool = ContactMessageTool(self)
        for row in self.data['proposals'].values():
            if row['state'] == 'sending':
                row['state'] = 'result_unknown'
        for row in self.data['history']:
            if row['state'] == 'sending':
                row['state'] = 'result_unknown'
        for row in self.data['attempts'].values():
            if row['state'] == 'sending':
                row['state'] = 'result_unknown'

    def bind_executor(self, safety, audit):
        # Private human-only Tool: never registered or offered to the LLM.
        self.safety = safety
        self.executor = ToolExecutor(safety, audit, CapabilityPolicy({Capability.CONTACT_MESSAGES: True}),
                                     permissions=self.admin.permissions)

    @staticmethod
    def validate_settings(value):
        if not isinstance(value, dict) or set(value) != set(SETTINGS):
            raise ValueError('Configuração de acompanhamento inválida.')
        if type(value['enabled']) is not bool or type(value['use_ai']) is not bool or value['mode'] not in {'suggest', 'prepare', 'rules'}:
            raise ValueError('Modo inválido.')
        if not isinstance(value['alias'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', value['alias']):
            raise ValueError('Escolha uma agenda já autorizada.')
        for key, minimum, maximum in [('interval_minutes', 5, 60), ('horizon_hours', 1, 168),
                                       ('quiet_start', 0, 23), ('quiet_end', 0, 23)]:
            if type(value[key]) is not int or not minimum <= value[key] <= maximum:
                raise ValueError('Intervalo ou horário inválido.')
        ZoneInfo(value['timezone'])

    def _save(self):
        self.data['history'] = self.data['history'][-100:]
        self.data['revision'] = uuid4().hex
        self.store.write(self.data)

    def settings(self, settings, revision):
        with self.lock:
            if revision != self.data['revision']:
                raise PermissionError('Configuração mudou; recarregue.')
            self.validate_settings(settings)
            if settings['enabled'] and settings['alias'] not in self.admin.calendar_view.state()['calendars']:
                raise PermissionError('Configure e autorize a agenda antes de acompanhar.')
            self.data['settings'] = deepcopy(settings)
            if any(row['alias'] != settings['alias'] for row in self.data['proposals'].values()):
                self.data['proposals'].clear()
            self.tickets.clear()
            self.next_scan = self.clock()
            self._save()
            return self.inventory()

    def channels(self):
        config = self.channel_store.read({})
        smtp = config.get('smtp')
        if smtp:
            ContactMessagingAdapter.validate_smtp(smtp)
        return {'email': {key: value for key, value in (smtp or {}).items() if key != 'password'},
                'email_ready': bool(smtp), 'whatsapp': 'manual', 'revision': digest(config),
                'note': 'WhatsApp abre a conversa com o texto revisado. E-mail envia pela conta SMTP que você configurar.'}

    def save_channels(self, smtp, revision):
        if revision != self.channels()['revision']:
            raise PermissionError('Conta de envio mudou; recarregue.')
        if smtp is not None:
            old = self.channel_store.read({}).get('smtp', {})
            smtp = deepcopy(smtp)
            if not smtp.get('password'):
                smtp['password'] = old.get('password', '')
            ContactMessagingAdapter.validate_smtp(smtp)
        self.channel_store.write({'smtp': smtp} if smtp else {})
        self.tickets.clear()
        return self.channels()

    def inventory(self):
        with self.lock:
            state = self.admin.calendar_view.state()
            readable = self.data['settings']['alias'] in state['calendars']
            now = self.clock()
            rows = []
            contacts = self.admin.contacts.inventory()['rows']
            channels = self.channels()
            if readable:
                for row in self.data['proposals'].values():
                    if row['alias'] != self.data['settings']['alias'] or (datetime.fromisoformat(row['event']['end']) <= now and row['state'] not in FINAL):
                        continue
                    if row['state'] in {'ignored', 'unavailable', 'accepted', 'user_reported_sent'}:
                        continue
                    if row.get('wake_at') and datetime.fromisoformat(row['wake_at']) > now:
                        continue
                    rows.append({**deepcopy(row), 'revision': digest(row), 'state_label': STATE_LABELS[row['state']]})
            return {'settings': deepcopy(self.data['settings']), 'revision': self.data['revision'],
                    'rows': sorted(rows, key=lambda row: row['event']['start']),
                    'history': [deepcopy(row) for row in self.data['history'] if readable and row['alias'] == self.data['settings']['alias']],
                    'rules': [{**deepcopy(rule), 'requires_review':
                        not any('local:' + contact['id'] == rule['contact_key'] and digest(contact) == rule['contact_digest'] for contact in contacts)
                        or digest(channels['email']) != rule['sender_digest']
                        or digest(MESSAGE_TEMPLATES[rule['purpose']]) != rule['template_digest']
                        or self.calendar_binding() != rule['calendar_binding']} for rule in self.data['rules']], 'contacts': contacts,
                    'calendars': state['calendars'], 'last_scan': self.data['last_scan'],
                    'error': self.data['last_error'] if readable else state['message'],
                    'channels': channels,
                    'note': 'Acompanha enquanto o JARVIS estiver aberto. Até 20 eventos por consulta; não confirma respostas recebidas nos aplicativos.'}

    def quiet(self):
        cfg = self.data['settings']
        hour = self.clock().astimezone(ZoneInfo(cfg['timezone'])).hour
        start, end = cfg['quiet_start'], cfg['quiet_end']
        return False if start == end else (start <= hour < end if start < end else hour >= start or hour < end)

    def tick(self):
        if self._closed or not self.data['settings']['enabled'] or self.clock() < self.next_scan or self.quiet():
            return
        try:
            with self.admin._lock:
                self.scan(manual=False)
        except Exception:
            self.data['last_error'] = 'Acompanhamento indisponível. Confira a conta e as permissões; não houve repetição de mensagens.'
            self.next_scan = self.clock() + timedelta(minutes=self.data['settings']['interval_minutes'])

    def _person(self, event, contacts):
        people = people_for(event, contacts)
        linked = self.data['links'].get(event['event_key'])
        if linked:
            return next((person for person in people if person['key'] == linked), None)
        participants = [person for person in people if person['in_event']]
        return participants[0] if len(participants) == 1 else None

    def scan(self, *, manual=True):
        with self.lock:
            cfg, now = self.data['settings'], self.clock()
            self.next_scan = now + timedelta(minutes=cfg['interval_minutes'])
            if not manual and (self._closed or not cfg['enabled'] or self.quiet()):
                return self.inventory()
            result = self.admin.calendar_view.events(cfg['alias'], 'upcoming')
            if result['state'] != 'loaded':
                self.data['last_error'] = result['message']
                return self.inventory()
            contacts = self.admin.contacts.inventory()['rows']
            seen = set()
            for event in result['rows']:
                start = datetime.fromisoformat(event['start'])
                if start < now or start > now + timedelta(hours=cfg['horizon_hours']):
                    continue
                key = event['event_key']
                seen.add(key)
                old = self.data['proposals'].get(key)
                person = self._person(event, contacts)
                person_key = person['key'] if person else ''
                unchanged = old and old['event']['fingerprint'] == event['fingerprint'] and old['contact_key'] == person_key
                if unchanged:
                    if old['state'] == 'unavailable':
                        old['state'], old['changed'] = 'review', True
                    if old['state'] == 'snoozed' and datetime.fromisoformat(old['wake_at']) <= now:
                        old['state'], old['wake_at'] = 'pending', None
                    continue
                if len(self.data['proposals']) >= 60 and key not in self.data['proposals']:
                    removable = next((id for id, row in self.data['proposals'].items() if row['state'] in {'unavailable', 'ignored', 'accepted'}), None)
                    if removable:
                        del self.data['proposals'][removable]
                    else:
                        break
                items = suggestions(event, result['rows'], person, now=now)
                base_action = next((item for item in items if item['purpose']), None)
                insights = []
                if cfg['use_ai'] and callable(self.admin.calendar_view.local_adviser):
                    advice = self.admin.calendar_view.advice(event, result['rows'], person, True)
                    items = advice['suggestions']
                    insights = advice['preparation'][:2]
                action = base_action or items[0]
                draft = message_draft(event, person, action['purpose']) if person and action['purpose'] else None
                changed = bool(old)
                row = {'id': key, 'event': event, 'alias': cfg['alias'], 'contact_key': person_key,
                    'contact': person['name'] if person else '', 'question': action['question'],
                    'reason': action['reason'], 'purpose': action['purpose'],
                    'draft': draft if cfg['mode'] != 'suggest' else None,
                    'state': old['state'] if old and old['state'] in FINAL else 'review' if changed else 'pending' if person else 'needs_contact',
                    'wake_at': None, 'changed': changed}
                row['insights'] = insights
                self.data['proposals'][key] = row
                if changed:
                    self._record(row, 'changed', 'O compromisso ou contato mudou; nenhuma mensagem foi reenviada.')
                self.data['seen'][key] = event['fingerprint']
            for key, row in self.data['proposals'].items():
                if key not in seen and row['state'] not in FINAL | {'ignored', 'snoozed'}:
                    row['state'] = 'unavailable'  # Absence is not proof of cancellation.
            self.data['last_scan'] = now.isoformat()
            self.data['last_error'] = 'Consulta limitada aos primeiros 20 eventos; pode haver outros.' if result['limit_reached'] else ''
            self.data['seen'] = {key: value for key, value in self.data['seen'].items() if key in self.data['proposals']}
            self._save()
            if cfg['enabled'] and cfg['mode'] == 'rules' and not self.quiet():
                for row in list(self.data['proposals'].values()):
                    self._run_rule(row)
            return self.inventory()

    def _record(self, row, state, message, receipt=''):
        self.data['history'].append({'id': uuid4().hex, 'proposal': row['id'], 'event_key': row['event']['event_key'],
            'alias': row['alias'],
            'contact_key': row['contact_key'], 'title': row['event']['title'], 'contact': row['contact'],
            'state': state, 'message': message, 'at': self.clock().isoformat(), 'receipt': receipt})

    def action(self, id, revision, action):
        row = self._row(id, revision)
        if action not in {'ignore', 'snooze', 'sent', 'not_sent'}:
            raise ValueError('Ação inválida.')
        if row['state'] in FINAL and action in {'ignore', 'snooze'}:
            raise PermissionError('Revise o resultado desta operação antes de continuar.')
        if action in {'sent', 'not_sent'} and row['state'] not in {'result_unknown', 'handed_off'}:
            raise PermissionError('Não há resultado pendente para revisar.')
        if action == 'snooze':
            row['state'], row['wake_at'] = 'snoozed', (self.clock() + timedelta(hours=1)).isoformat()
        elif action == 'ignore':
            row['state'] = 'ignored'
        else:
            row['state'] = 'user_reported_sent' if action == 'sent' else 'pending'
            # Explicit user report clears the uncertain fence, not a retry.
            for record in self.data['history']:
                if record['proposal'] == id and record['state'] in FINAL:
                    record['state'] = 'user_reported_sent' if action == 'sent' else 'user_reported_not_sent'
            key = self.attempt_key(row)
            if key in self.data['attempts']:
                self.data['attempts'][key]['state'] = 'user_reported_sent' if action == 'sent' else 'user_reported_not_sent'
        self._record(row, action, 'Decisão registrada pelo usuário.')
        self.tickets.clear()
        self._save()
        return self.inventory()

    def link(self, id, revision, contact_key):
        row = self._row(id, revision)
        known = {'local:' + item['id'] for item in self.admin.contacts.inventory()['rows']}
        if contact_key not in known:
            raise ValueError('Escolha um contato cadastrado neste perfil.')
        self.data['links'][row['event']['event_key']] = contact_key
        self.tickets.clear()
        self._save()
        return self.scan()

    def _row(self, id, revision):
        row = self.data['proposals'].get(id) if isinstance(id, str) else None
        if row is None or digest(row) != revision:
            raise PermissionError('Proposta alterada ou antiga; recarregue.')
        return row

    def _fresh(self, row):
        result, event, _people, person = self.admin.calendar_view._context(row['alias'], 'upcoming',
            row['event']['fingerprint'], row['contact_key'])
        if event['event_key'] != row['event']['event_key'] or person is None:
            raise PermissionError('Compromisso ou destinatário alterado.')
        return person

    def prepare(self, id, revision, channel, subject, body, *, actor, rule=None):
        if self.executor is None or (actor is None and rule is None) or self._closed:
            raise PermissionError('Aprovação indisponível nesta sessão.')
        row = self._row(id, revision)
        if row['state'] not in {'pending', 'review', 'rejected'}:
            raise PermissionError('Proposta pausada, concluída ou com resultado pendente.')
        if self.data['attempts'].get(self.attempt_key(row), {}).get('state') in FINAL:
            raise PermissionError('Já houve uma tentativa neste compromisso; verifique o histórico.')
        person = self._fresh(row)
        draft = message_draft(row['event'], person, row['purpose'], channel)
        if draft['channel'] == 'email' and not self.channels()['email_ready']:
            raise MessageSetupRequiredError('Conta de envio ausente.')
        if not isinstance(subject, str) or not isinstance(body, str) or len(subject) > 240 or len(body) > 4096:
            raise ValueError('Mensagem acima do limite.')
        if any(char in subject for char in '\r\n'):
            raise ValueError('Assunto inválido.')
        if body:
            draft['body'] = body
        if subject:
            draft['subject'] = subject
        if not draft['body'].strip():
            raise ValueError('Mensagem vazia.')
        token = uuid4().hex
        self.tickets = {key: value for key, value in self.tickets.items() if value['expires'] > self.clock() and value['actor'] is not actor}
        if len(self.tickets) >= 16:
            raise PermissionError('Muitas revisões pendentes.')
        self.tickets[token] = {'actor': actor, 'expires': self.clock() + timedelta(minutes=2),
            'proposal': id, 'proposal_revision': digest(row), 'draft': draft,
            'revision': self._revision(), 'rule': deepcopy(rule), 'used': False}
        return {'token': token, 'draft': deepcopy(draft), 'action':
                'Abrir conversa no WhatsApp' if draft['channel'] == 'whatsapp' else 'Enviar este e-mail',
                'notice': 'Aprovação vale somente para este destinatário e este texto. WhatsApp exige concluir o envio no aplicativo.'}

    def _revision(self):
        return (self.admin.calendar_view._revision(), self.channels()['revision'], digest(self.data['settings']), digest(self.data['rules']))

    def validated(self, token):
        ticket = self.tickets.get(token) if isinstance(token, str) else None
        if (self._closed or ticket is None or ticket['used'] or ticket['expires'] <= self.clock()
                or ticket['revision'] != self._revision()):
            raise PermissionError('Aprovação vencida ou alterada; revise novamente.')
        row = self._row(ticket['proposal'], ticket['proposal_revision'])
        if ticket['rule'] and not self._rule_eligible(row, ticket['rule']):
            raise PermissionError('A regra deixou de autorizar esta mensagem.')
        return ticket

    def approve(self, token, *, actor):
        ticket = self.validated(token)
        if ticket['rule'] is not None or actor is None or ticket['actor'] is not actor:
            raise PermissionError('Aprovação pertence a outra sessão.')
        row = self.data['proposals'][ticket['proposal']]
        person = self._fresh(row)
        recipient = person['whatsapp'] if ticket['draft']['channel'] == 'whatsapp' else person['email']
        if recipient != ticket['draft']['recipient']:
            raise PermissionError('Destinatário mudou; revise novamente.')
        return self._execute(token)

    def cancel(self, token, *, actor):
        ticket = self.tickets.get(token)
        if ticket and ticket['actor'] is actor:
            del self.tickets[token]
        return {'cancelled': True}

    def _execute(self, token):
        prepared = self.tool.prepare(token=token)
        interaction = uuid4().hex
        grant = self.safety.grant_explicit_authorization(self.tool.name, prepared.target, self.tool.risk,
            interaction, arguments_digest=digest_arguments({'token': token}), preparation_digest=prepared.binding_digest)
        if grant is None:
            raise PermissionError('Envio não autorizado pelo executor.')
        result = self.executor.execute_with_grant(self.tool, grant, interaction, token=token)
        if result.status != 'success':
            self.tickets.pop(token, None)
        return {'status': result.status, 'message': result.content, 'inventory': self.inventory()}

    def save_rule(self, rule, revision, accepted):
        if revision != self.data['revision'] or type(accepted) is not bool:
            raise PermissionError('Recarregue antes de autorizar a regra.')
        fields = {'id', 'enabled', 'contact_key', 'purpose', 'channel', 'lead_minutes', 'daily_limit', 'start_hour', 'end_hour'}
        if not isinstance(rule, dict) or set(rule) != fields:
            raise ValueError('Regra inválida.')
        if (not re.fullmatch(r'[a-f0-9]{32}', rule['id']) or type(rule['enabled']) is not bool
                or rule['purpose'] not in {'confirm', 'notify'} or rule['channel'] not in {'auto', 'email'}):
            raise ValueError('Regra fora do escopo.')
        for key, minimum, maximum in [('lead_minutes', 15, 1440), ('daily_limit', 1, 5), ('start_hour', 0, 23), ('end_hour', 1, 24)]:
            if type(rule[key]) is not int or not minimum <= rule[key] <= maximum:
                raise ValueError('Limite da regra inválido.')
        if rule['start_hour'] >= rule['end_hour'] or rule['contact_key'] not in {'local:' + row['id'] for row in self.admin.contacts.inventory()['rows']}:
            raise ValueError('Escolha um contato e um horário válido.')
        if rule['enabled'] and not accepted:
            raise PermissionError('Autorize explicitamente o modelo e os limites desta regra.')
        others = [row for row in self.data['rules'] if row['id'] != rule['id']]
        if len(others) >= 20:
            raise ValueError('Limite de vinte regras.')
        contact = next(row for row in self.admin.contacts.inventory()['rows'] if 'local:' + row['id'] == rule['contact_key'])
        self.data['rules'] = others + [{**deepcopy(rule), 'contact_digest': digest(contact),
            'sender_digest': digest(self.channels()['email']), 'template_digest': digest(MESSAGE_TEMPLATES[rule['purpose']]),
            'calendar_binding': self.calendar_binding()}]
        self.tickets.clear()
        self._save()
        return self.inventory()

    def delete_rule(self, id, revision):
        if revision != self.data['revision']:
            raise PermissionError('Regra mudou; recarregue.')
        self.data['rules'] = [row for row in self.data['rules'] if row['id'] != id]
        self.tickets.clear()
        self._save()
        return self.inventory()

    def _rule_eligible(self, row, rule):
        cfg, now = self.data['settings'], self.clock()
        if not any(digest(approved) == digest(rule) for approved in self.data['rules']):
            return False
        contact = next((person for person in self.admin.contacts.inventory()['rows'] if 'local:' + person['id'] == rule['contact_key']), None)
        if (contact is None or digest(contact) != rule['contact_digest'] or digest(self.channels()['email']) != rule['sender_digest']
                or digest(MESSAGE_TEMPLATES[rule['purpose']]) != rule['template_digest']
                or self.calendar_binding() != rule['calendar_binding']):
            return False
        if (self._closed or not cfg['enabled'] or cfg['mode'] != 'rules' or self.quiet() or not rule['enabled']
                or row['state'] != 'pending' or row['changed'] or row['contact_key'] != rule['contact_key']
                or row['purpose'] != rule['purpose'] or row['event']['all_day'] or row['event'].get('status') != 'confirmed'
                or self.admin.permissions.decision('tool:contact_message_send').mode != 'allow'):
            return False
        local_now = now.astimezone(ZoneInfo(cfg['timezone']))
        if not rule['start_hour'] <= local_now.hour < rule['end_hour']:
            return False
        remaining = (datetime.fromisoformat(row['event']['start']) - now).total_seconds()
        if not 0 < remaining <= rule['lead_minutes'] * 60:
            return False
        history = list(self.data['attempts'].values())
        if self.attempt_key(row) in self.data['attempts']:
            return False
        attempts_today = [record for record in history if record['state'] in FINAL | {'rejected'}
            and datetime.fromisoformat(record['at']).astimezone(ZoneInfo(cfg['timezone'])).date() == local_now.date()]
        if len(attempts_today) >= 5 or sum(record['contact_key'] == rule['contact_key'] for record in attempts_today) >= rule['daily_limit']:
            return False
        # Browser handoffs never run in the background. No implicit email fallback.
        person = self._person(row['event'], self.admin.contacts.inventory()['rows'])
        return bool(person and (rule['channel'] == 'email' or not person['whatsapp']) and person['email'] and self.channels()['email_ready'])

    def _run_rule(self, row):
        for rule in self.data['rules']:
            if self._rule_eligible(row, rule):
                try:
                    preview = self.prepare(row['id'], digest(row), rule['channel'], '', '', actor=None, rule=rule)
                    self._execute(preview['token'])
                except Exception:
                    self.data['last_error'] = 'Uma regra não pôde executar; confira o histórico. Nenhuma repetição automática.'
                break

    def disconnect(self, actor):
        self.tickets = {key: value for key, value in self.tickets.items() if value['actor'] is not actor}

    @staticmethod
    def attempt_key(row):
        return digest({'event': row['event']['event_key'], 'contact': row['contact_key']})

    def calendar_binding(self):
        alias = self.data['settings']['alias']
        tool = self.admin.registry.get('google_calendar_list_events')
        return digest({'alias': alias, 'calendar': getattr(tool, 'calendar_aliases', {}).get(alias),
            'accounts': self.admin.accounts.inventory()['revision'] if self.admin.accounts is not None else None})

    def close(self):
        self._closed = True
        self.tickets.clear()
        self._authorized.clear()


class ContactMessageTool(Tool):
    name = 'contact_message_send'
    description = 'Executa somente uma mensagem aprovada na interface ou por regra explícita.'
    risk = RiskLevel.EXTERNAL_EFFECT
    requires_authorization_receipt = True
    parameters = {'type': 'object', 'properties': {'token': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}},
                  'required': ['token'], 'additionalProperties': False}

    def __init__(self, service):
        self.service = service

    def get_target(self, **kwargs):
        return self.prepare(**kwargs).target

    def prepare(self, token):
        ticket = self.service.validated(token)
        binding = digest({'token': token, 'draft': ticket['draft'], 'proposal': ticket['proposal_revision'],
                          'revision': ticket['revision'], 'rule': ticket['rule']})
        return ToolPreparation('contact_message:' + binding, token, binding)

    def preflight_prepared(self, preparation, **kwargs):
        if self.prepare(**kwargs).binding_digest != preparation.binding_digest:
            raise PermissionError('Mensagem alterada após revisão.')

    def bind_authorized(self, preparation, authorization=None):
        if not isinstance(authorization, AuthorizationReceipt) or not authorization.claim(self.name, preparation.target, preparation.binding_digest):
            raise PermissionError('Envio exige autorização vinculada do executor.')
        if self.prepare(token=preparation.context).binding_digest != preparation.binding_digest:
            raise PermissionError('Mensagem mudou após autorização.')
        nonce = uuid4().hex
        self.service._authorized.add(nonce)
        return ToolPreparation(preparation.target, (preparation.context, nonce), preparation.binding_digest)

    def execute_authorized(self, preparation):
        service = self.service
        if not isinstance(preparation.context, tuple) or len(preparation.context) != 2:
            raise PermissionError('Artefato autorizado ausente.')
        token, nonce = preparation.context
        if nonce not in service._authorized:
            raise PermissionError('Artefato já consumido.')
        service._authorized.remove(nonce)
        ticket = service.validated(token)
        if self.prepare(token).binding_digest != preparation.binding_digest:
            raise PermissionError('Mensagem alterada.')
        row = service.data['proposals'][ticket['proposal']]
        smtp = service.channel_store.read({}).get('smtp')
        ticket['used'] = True
        row['state'] = 'sending'
        attempt_key = service.attempt_key(row)
        if len(service.data['attempts']) >= 1000 and attempt_key not in service.data['attempts']:
            raise PermissionError('Limite do histórico de tentativas; envio bloqueado.')
        service.data['attempts'][attempt_key] = {'state': 'sending', 'at': service.clock().isoformat(), 'contact_key': row['contact_key']}
        service._record(row, 'sending', 'Tentativa iniciada; aguarde o resultado.')
        record = service.data['history'][-1]
        service._save()  # Durable fence before opening an app or sending bytes.
        try:
            outcome = service.adapter.send(ticket['draft'], smtp)
            if outcome.get('state') not in {'accepted', 'handed_off'}:
                raise RuntimeError('Recibo inválido.')
        except MessageRejectedError:
            outcome = {'state': 'rejected', 'message': 'Operação recusada. Confira a conexão; não houve repetição automática.', 'receipt': ''}
        except Exception:
            outcome = {'state': 'result_unknown', 'message': 'Resultado incerto. Verifique no aplicativo antes de tentar novamente; não houve repetição automática.', 'receipt': ''}
        row['state'] = record['state'] = outcome['state']
        service.data['attempts'][attempt_key]['state'] = outcome['state']
        record['message'], record['receipt'] = outcome['message'], outcome.get('receipt', '')
        service._save()
        service.tickets.pop(token, None)
        if outcome['state'] not in {'accepted', 'handed_off'}:
            raise RuntimeError(outcome['message'])
        return outcome['message']

    def execute(self, **kwargs):
        raise PermissionError('Envio exige prévia, autorização vinculada e executor.')
