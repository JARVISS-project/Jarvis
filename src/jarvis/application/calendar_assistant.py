"""Grounded suggestions and editable drafts, with no messaging authority."""
from __future__ import annotations

from datetime import datetime
import re
import unicodedata

from jarvis.application.contacts import email_address


PREPARATION = {
    'agenda': 'Preparar uma pauta curta com o objetivo do encontro.',
    'documents': 'Separar os documentos mencionados no compromisso.',
    'questions': 'Anotar as perguntas que você quer fazer.',
    'materials': 'Conferir os materiais necessários para o encontro.',
    'location': 'Conferir o local ou o link antes de começar.',
    'availability': 'Revisar sua disponibilidade para esse horário.',
}

MESSAGE_TEMPLATES = {
    'confirm': 'Olá, {name}! Podemos confirmar nosso compromisso {reference}?',
    'notify': 'Olá, {name}! Passando para lembrar do compromisso {reference}. O horário continua bom para você?',
    'reschedule': 'Olá, {name}! Podemos combinar outro horário para o compromisso {reference}? Qual seria melhor para você?',
    'follow_up': 'Olá, {name}! Sobre o compromisso {reference}, podemos alinhar os próximos passos?',
}


def people_for(event, contacts):
    people, linked = [], set()
    for index, person in enumerate(event.get('participants', [])):
        if person.get('self') or person.get('resource'):
            continue
        try:
            email = email_address(person.get('email', ''))
        except ValueError:
            email = ''
        matches = [row for row in contacts if email and row['email'] == email]
        contact = matches[0] if len(matches) == 1 else None
        if contact:
            if contact['id'] in linked:
                continue
            linked.add(contact['id'])
        people.append({'key': 'local:' + contact['id'] if contact else f'event:{index}',
            'name': contact['name'] if contact else person.get('name') or email or 'Participante sem nome',
            'email': contact['email'] if contact else email,
            'whatsapp': contact['whatsapp'] if contact else '',
            'response': person.get('response', 'unknown'), 'in_event': True})
    for contact in contacts:
        if contact['id'] not in linked:
            people.append({'key': 'local:' + contact['id'], **{key: contact[key] for key in ('name', 'email', 'whatsapp')},
                           'response': 'unknown', 'in_event': False})
    return people


def suggestions(event, rows, person=None, now=None):
    start, end = datetime.fromisoformat(event['start']), datetime.fromisoformat(event['end'])
    now = now or datetime.now(start.tzinfo)
    ended = end <= now
    result = []
    def add(id, question, reason, purpose=None):
        result.append({'id': id, 'question': question, 'reason': reason, 'purpose': purpose})

    if person and (person['whatsapp'] or person['email']):
        name = person['name']
        if ended:
            add('follow_up', f'Devo preparar um acompanhamento para {name}?',
                'O horário registrado já terminou.', 'follow_up')
        elif person['response'] in {'needsAction', 'tentative'}:
            add('confirm', f'Devo confirmar o compromisso com {name}?',
                'A participação consta como pendente ou provisória no evento.', 'confirm')
        elif person['response'] == 'declined':
            add('confirm', f'Quer combinar outro horário com {name}?',
                'Essa pessoa recusou o convite; vale revisar a disponibilidade.', 'reschedule')
        else:
            add('notify', f'Devo preparar um aviso para {name}?',
                'Há um contato disponível; o envio depende da revisão da mensagem.', 'notify')
    if not ended:
        if any(person.get('self') and person.get('response') in {'needsAction', 'tentative'}
               for person in event.get('participants', [])):
            add('invitation', 'Quer revisar a resposta ao convite desta agenda?',
                'O convite desta agenda está pendente ou provisório. Nenhuma resposta será alterada aqui.')
        conflicts = [row for row in rows if row['fingerprint'] != event['fingerprint']
            and not row['all_day'] and not event['all_day'] and not row.get('transparent') and not event.get('transparent')
            and datetime.fromisoformat(row['start']) < end and datetime.fromisoformat(row['end']) > start]
        if conflicts:
            add('conflict', 'Quer revisar uma coincidência de horários?',
                f"Há sobreposição com {len(conflicts)} compromisso(s) entre os eventos carregados.")
        if not event['location']:
            add('location', 'Quer conferir o local ou o link?', 'O evento não informa um local.')
        add('prepare', 'Quer preparar uma pauta ou separar o que precisa levar?',
            'Você pode usar os detalhes do compromisso para se preparar.')
    else:
        add('prepare', 'Quer registrar os próximos passos deste encontro?',
            'O horário registrado já terminou; vale revisar pendências.')
    return result


def preparation_for(event):
    value = unicodedata.normalize('NFKD', event['title'] + ' ' + event.get('notes', '')).encode('ascii', 'ignore').decode().casefold()
    if re.search(r'\b(consulta|entrevista|documento|exame)\b', value):
        return ['documents', 'questions', 'location']
    return ['agenda', 'questions', 'location']


def message_draft(event, person, purpose, channel='auto'):
    if purpose not in {'confirm', 'notify', 'reschedule', 'follow_up'}:
        raise ValueError('Tipo de mensagem inválido.')
    if channel not in {'auto', 'whatsapp', 'email'}:
        raise ValueError('Canal inválido.')
    if channel == 'auto':
        channel = 'whatsapp' if person['whatsapp'] else 'email'
    recipient = person['whatsapp'] if channel == 'whatsapp' else person['email']
    if not recipient:
        raise ValueError('Este contato não possui o canal escolhido.')
    start = datetime.fromisoformat(event['start'])
    when = f'{start:%d/%m/%Y}' + ('' if event['all_day'] else f' às {start:%H:%M}')
    reference = f'“{event["title"]}”, em {when}'
    return {'channel': channel, 'recipient': recipient, 'name': person['name'],
            'subject': 'Sobre o compromisso: ' + event['title'],
            'body': MESSAGE_TEMPLATES[purpose].format(name=person['name'], reference=reference),
            'message': 'Rascunho para revisão. Nenhuma mensagem foi enviada.'}
