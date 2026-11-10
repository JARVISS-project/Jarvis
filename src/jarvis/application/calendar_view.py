"""Calendar display through the existing read Tool and its full executor."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from jarvis.application.calendar_assistant import people_for, suggestions, preparation_for, PREPARATION, message_draft


class CalendarContextChangedError(PermissionError):
    pass


class CalendarViewService:
    def __init__(self, administration, execute_tool):
        self.admin = administration
        self.execute_tool = execute_tool
        self.local_adviser = None
        self.clock = lambda: datetime.now(timezone.utc)

    def _revision(self):
        return (self.admin.permissions.read()[1],
                self.admin.accounts.inventory()['revision'] if self.admin.accounts is not None else None,
                self.admin.contacts.inventory()['revision'])

    def state(self) -> dict:
        admin = self.admin
        tool = admin.registry.get('google_calendar_list_events')
        aliases = getattr(tool, 'calendar_aliases', {})
        if not admin.config.google_calendar_enabled or not aliases or not callable(self.execute_tool):
            return {'state': 'setup', 'calendars': [],
                    'message': 'Conecte sua conta, salve uma agenda e ative as consultas em Contas e conexões. Se acabou de configurar, reinicie o JARVIS.'}
        try:
            if admin.capability_policy is not None:
                admin.capability_policy.require_tool(tool.name)
            choices = []
            for alias, calendar in aliases.items():
                try:
                    decision = admin.permissions.check_tool(tool, {'calendar_id': calendar})
                except PermissionError:
                    continue
                if decision.mode == 'allow':
                    choices.append(alias)
        except PermissionError:
            choices = []
        return {'state': 'ready' if choices else 'blocked', 'calendars': sorted(choices),
                'message': 'Escolha o período e carregue seus compromissos.' if choices else
                    'As consultas estão bloqueadas ou exigem confirmação. Revise Permissões ou consulte pela Conversa.'}

    def events(self, alias: str, period: str) -> dict:
        if not isinstance(alias, str) or not isinstance(period, str) or period not in {'today', 'tomorrow', 'upcoming'}:
            raise ValueError('Consulta de agenda inválida.')
        state = self.state()
        if state['state'] != 'ready':
            return state
        if alias not in state['calendars']:
            return {'state': 'blocked', 'calendars': [],
                    'message': 'Esta agenda não está disponível para consulta. Recarregue a tela e escolha uma agenda autorizada.'}
        tool = self.admin.registry.get('google_calendar_list_events')
        arguments = {'calendar_id': tool.resolve_calendar(alias), 'period': period,
                     'max_results': 20, 'output_format': 'table'}
        # Do not strand a confirmation in the text channel from this worker.
        # The executor independently revalidates policy, scope and identity.
        if self.admin.permissions.check_tool(tool, arguments).mode != 'allow':
            return {'state': 'blocked', 'calendars': [],
                    'message': 'A permissão mudou. Revise Permissões ou consulte pela Conversa.'}
        result = self.execute_tool(tool, arguments)
        if result.status != 'success' or not result.authorized:
            return {'state': 'error', 'message': 'Não foi possível consultar a agenda. Verifique a conexão e as permissões e tente novamente.'}
        data = json.loads(result.content)
        return {**data, 'state': 'loaded', 'calendar': alias}

    def _context(self, alias, period, fingerprint, contact_key):
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint) or not isinstance(contact_key, str):
            raise ValueError('Seleção de compromisso inválida.')
        data = self.events(alias, period)
        if data['state'] != 'loaded':
            raise PermissionError('Recarregue a agenda e confira suas permissões.')
        event = next((row for row in data['rows'] if row['fingerprint'] == fingerprint), None)
        if event is None:
            raise CalendarContextChangedError('Seleção de compromisso antiga.')
        contacts = self.admin.contacts.inventory()
        people = people_for(event, contacts['rows'])
        selected = next((person for person in people if person['key'] == contact_key), None)
        if contact_key and selected is None:
            raise CalendarContextChangedError('Seleção de contato antiga.')
        if not contact_key:
            participants = [person for person in people if person['in_event']]
            selected = participants[0] if len(participants) == 1 else None
        return data, event, people, selected

    def assist(self, alias, period, fingerprint, contact_key, use_ai):
        if type(use_ai) is not bool:
            raise ValueError('Opção de IA inválida.')
        data, event, people, selected = self._context(alias, period, fingerprint, contact_key)
        advice = self.advice(event, data['rows'], selected, use_ai, alias=alias)
        return {'event': event, 'people': people, 'selected': selected['key'] if selected else '', **advice,
                'notice': 'Nenhum contato foi feito. Preparar uma mensagem não confirma presença nem altera a agenda.'}

    def advice(self, event, rows, selected, use_ai, *, alias=None):
        items = suggestions(event, rows, selected, now=self.clock())
        prep = preparation_for(event)
        mode = 'Sugestões baseadas nos dados do evento.'
        if use_ai:
            if not callable(self.local_adviser):
                mode = 'IA local indisponível. Mantidas as sugestões baseadas nos dados do evento.'
            else:
                revision = self._revision()
                # Only this selected event; contact addresses and the rest of
                # the agenda are not model inputs. Names are joined in code.
                context = {key: event[key] for key in ('title', 'start', 'end', 'all_day', 'location', 'notes')}
                context['participant'] = {'response': selected['response']} if selected else None
                context['candidates'] = [{'id': item['id'], 'reason': item['reason']} for item in items]
                context['preparation_options'] = PREPARATION
                try:
                    answer = self.local_adviser(context)
                    ids = answer.get('suggestion_ids', [])
                    proposed = answer.get('preparation_ids', [])
                    if not isinstance(ids, list) or not isinstance(proposed, list):
                        raise ValueError
                    ranked = [next(item for item in items if item['id'] == key) for key in dict.fromkeys(ids[:4]) if key in {item['id'] for item in items}]
                    selected_prep = [key for key in dict.fromkeys(proposed[:4]) if key in PREPARATION]
                    if not ranked and not selected_prep:
                        raise ValueError
                    items = ranked or items
                    prep = selected_prep or prep
                    mode = 'Sugestões priorizadas pela IA local. Revise antes de usar.'
                except Exception:
                    mode = 'A IA local não respondeu de forma válida. Mantidas as sugestões do evento.'
                # Revocation or resource changes while the model was thinking wins.
                current = self.state()
                if current['state'] != 'ready' or (alias is not None and alias not in current['calendars']) or self._revision() != revision:
                    raise CalendarContextChangedError('Permissões alteradas durante a análise.')
        return {'suggestions': items, 'preparation': [PREPARATION[key] for key in prep],
                'ai_available': callable(self.local_adviser), 'mode': mode}

    def draft(self, alias, period, fingerprint, contact_key, purpose, channel):
        _data, event, _people, selected = self._context(alias, period, fingerprint, contact_key)
        if selected is None:
            raise ValueError('Escolha explicitamente um contato.')
        return message_draft(event, selected, purpose, channel)
