"""Explicit local write requests; private payload never reaches an LLM."""
from __future__ import annotations

import json
import re

USAGE = ('Use uma intenção explícita: /agenda criar '
         '{"calendario":"teste","titulo":"Título","inicio":"2026-10-02T14:00:00-03:00",'
         '"fim":"2026-10-02T15:00:00-03:00","timezone":"America/Sao_Paulo"}. '
         'Para alterar, use /agenda alterar com calendario, evento (ID selecionado explicitamente) '
         'e titulo OU inicio/fim/timezone. Sempre haverá prévia e confirmação.')


def looks_like_write(message: str) -> bool:
    return (message.casefold().startswith('/agenda') or bool(re.search(
        r'\b(?:criar|crie|marcar|marque|agendar|agende|alterar|altere|mudar|mude)\b.*'
        r'\b(?:agenda|calendário|calendario|evento|compromisso)\b', message.casefold())))


def dispatch(harness, message: str) -> str | None:
    if not looks_like_write(message):
        return None
    harness.last_response_private_calendar = True
    match = re.fullmatch(r'/agenda (criar|alterar) (\{.*\})', message.strip(), re.DOTALL)
    if match is None:
        return USAGE
    action, raw = match.groups()
    try:
        def unique(pairs):
            value = dict(pairs)
            if len(value) != len(pairs):
                raise ValueError
            return value
        values = json.loads(raw, object_pairs_hook=unique)
        allowed = {'calendario', 'titulo', 'inicio', 'fim', 'timezone'}
        if action == 'alterar':
            allowed.add('evento')
        if (not isinstance(values, dict) or set(values) - allowed
                or not all(isinstance(v, str) for v in values.values())):
            return USAGE
        tool = harness.tool_registry.get('google_calendar_create_event' if action == 'criar'
                                         else 'google_calendar_update_event')
        if tool is None or not hasattr(tool, 'calendar_aliases'):
            return 'Calendar WRITE desligado ou indisponível; consentimento e calendário de teste explícitos são necessários.'
        calendar = tool.calendar_aliases.get(values.get('calendario'))
        if calendar is None:
            return 'Calendário não permitido explicitamente para escrita.'
        mapping = {'titulo': 'summary', 'inicio': 'start', 'fim': 'end',
                   'timezone': 'timezone', 'evento': 'event_id'}
        args = {mapping[k]: v for k, v in values.items() if k != 'calendario'}
        args['calendar_id'] = calendar
        if action == 'alterar':
            if 'event_id' not in args:
                return USAGE
            args['selection'] = tool.register_selection(calendar, args['event_id'])
        return harness._execute_tool(tool, args).content
    except (ValueError, TypeError, KeyError, PermissionError):
        return 'Pedido inválido ou consentimento insuficiente. ' + USAGE
