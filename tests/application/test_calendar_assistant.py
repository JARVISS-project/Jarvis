"""Real Calendar read/executor and protected contacts; synthetic events/model."""
import json

import pytest

from jarvis.application.contacts import ContactService
from tests.integration.test_calendar_read_23ba import NOW
from tests.integration.test_administration_23bh import change
from tests.ui.test_administrative_widgets_23bh import close_system
from tests.ui.test_calendar_table import system


def setup_context(tmp_path, whatsapp='+55 11 99999-9999'):
    values = system(tmp_path)
    app, h, service, controller, window, boundary = values
    service.calendar_view.clock = lambda: NOW
    boundary.items[0]['attendees'] = [
        {'displayName': 'João do evento', 'email': 'joao@example.invalid', 'responseStatus': 'needsAction'},
        {'displayName': 'Sala', 'email': 'room@example.invalid', 'resource': True},
        {'email': 'owner@example.invalid', 'self': True, 'responseStatus': 'accepted'}]
    service.contacts.save(None, 'João', 'JOAO@example.invalid', whatsapp, service.contacts.inventory()['revision'])
    events = service.dispatch('calendar.events', {'alias': 'principal', 'period': 'today'})
    event = next(row for row in events['rows'] if not row['all_day'])
    args = {'alias': 'principal', 'period': 'today', 'fingerprint': event['fingerprint'], 'contact_key': ''}
    return values, args


def test_contact_match_whatsapp_first_and_email_fallback_without_ai_or_messages(tmp_path):
    values, args = setup_context(tmp_path)
    app, h, service, controller, window, boundary = values
    try:
        data = service.dispatch('calendar.assist', {**args, 'use_ai': False})
        assert data['selected'].startswith('local:')
        assert len(data['people']) == 1 and data['people'][0]['name'] == 'João'
        assert any('confirmar' in row['question'] for row in data['suggestions'])
        draft = service.dispatch('calendar.draft', {**args, 'contact_key': data['selected'], 'purpose': 'confirm', 'channel': 'auto'})
        assert draft['channel'] == 'whatsapp' and draft['recipient'] == '+5511999999999'
        assert 'Podemos confirmar' in draft['body'] and '14:00' in draft['body']
        email = service.dispatch('calendar.draft', {**args, 'contact_key': data['selected'], 'purpose': 'confirm', 'channel': 'email'})
        assert email['channel'] == 'email' and email['recipient'] == 'joao@example.invalid'
        assert h.memory.get_history() == []
        audit = (tmp_path / 'audit.log').read_text()
        assert 'joao@example.invalid' not in audit and 'João' not in audit and '99999' not in audit
        assert all(path.startswith('/calendars/') for path, _ in boundary.calls)
    finally:
        close_system(h, controller, window)


def test_email_only_contact_uses_email(tmp_path):
    values, args = setup_context(tmp_path, whatsapp='')
    app, h, service, controller, window, _ = values
    try:
        draft = service.dispatch('calendar.draft', {**args, 'purpose': 'notify', 'channel': 'auto'})
        assert draft['channel'] == 'email' and draft['recipient'] == 'joao@example.invalid'
        with pytest.raises(ValueError):
            service.dispatch('calendar.draft', {**args, 'purpose': 'notify', 'channel': 'whatsapp'})
    finally:
        close_system(h, controller, window)


def test_local_ai_explicit_minimal_context_and_only_known_suggestion_ids(tmp_path):
    values, args = setup_context(tmp_path)
    app, h, service, controller, window, _ = values
    contexts = []
    def adviser(context):
        contexts.append(context)
        return {'suggestion_ids': ['send_now', 'confirm', 'confirm'],
                'preparation_ids': ['questions', 'read_private_files']}
    service.calendar_view.local_adviser = adviser
    try:
        service.dispatch('calendar.assist', {**args, 'use_ai': False})
        assert contexts == []
        data = service.dispatch('calendar.assist', {**args, 'use_ai': True})
        assert len(contexts) == 1 and [row['id'] for row in data['suggestions']] == ['confirm']
        assert len(data['preparation']) == 1
        context = json.dumps(contexts)
        assert 'example.invalid' not in context and '99999' not in context and 'Planejamento' not in context
        assert contexts[0]['participant'] == {'response': 'needsAction'} and h.memory.get_history() == []
    finally:
        close_system(h, controller, window)


@pytest.mark.parametrize('answer', [None, 'enviei tudo', {'suggestion_ids': [{}]}, {'suggestion_ids': ['invented'], 'preparation_ids': []}])
def test_invalid_ai_never_invents_actions_or_changes_contact(tmp_path, answer):
    values, args = setup_context(tmp_path)
    app, h, service, controller, window, _ = values
    service.calendar_view.local_adviser = lambda context: answer
    try:
        data = service.dispatch('calendar.assist', {**args, 'use_ai': True})
        assert data['selected'].startswith('local:') and data['suggestions']
        assert all(row['id'] in {'confirm', 'location', 'prepare'} for row in data['suggestions'])
        assert 'enviei tudo' not in str(data)
    finally:
        close_system(h, controller, window)


def test_changed_event_and_revocation_refuse_analysis_or_draft(tmp_path):
    values, args = setup_context(tmp_path)
    app, h, service, controller, window, boundary = values
    called = []
    service.calendar_view.local_adviser = lambda context: called.append(context)
    try:
        boundary.items[0]['summary'] = 'Evento alterado'
        with pytest.raises(PermissionError):
            service.dispatch('calendar.assist', {**args, 'use_ai': True})
        assert called == []
        calls = len(boundary.calls)
        change(service, 'tool:google_calendar_list_events', 'block')
        with pytest.raises(PermissionError):
            service.dispatch('calendar.draft', {**args, 'purpose': 'notify', 'channel': 'auto'})
        assert len(boundary.calls) == calls
    finally:
        close_system(h, controller, window)


def test_permission_change_while_ai_runs_wins(tmp_path):
    values, args = setup_context(tmp_path)
    app, h, service, controller, window, _ = values
    def adviser(context):
        change(service, 'tool:google_calendar_list_events', 'block')
        return {'suggestion_ids': ['confirm'], 'preparation_ids': []}
    service.calendar_view.local_adviser = adviser
    try:
        with pytest.raises(PermissionError):
            service.dispatch('calendar.assist', {**args, 'use_ai': True})
    finally:
        close_system(h, controller, window)


def test_same_name_does_not_link_wrong_contact(tmp_path):
    values, args = setup_context(tmp_path)
    app, h, service, controller, window, boundary = values
    try:
        boundary.items[0]['attendees'][0]['email'] = 'other@example.invalid'
        boundary.items[0]['attendees'][0]['displayName'] = 'João'
        events = service.dispatch('calendar.events', {'alias': 'principal', 'period': 'today'})
        args['fingerprint'] = next(row['fingerprint'] for row in events['rows'] if not row['all_day'])
        data = service.dispatch('calendar.assist', {**args, 'use_ai': False})
        assert data['selected'] == 'event:0'
        assert data['people'][0]['whatsapp'] == '' and data['people'][0]['email'] == 'other@example.invalid'
        draft = service.dispatch('calendar.draft', {**args, 'purpose': 'notify', 'channel': 'auto'})
        assert draft['channel'] == 'email' and draft['recipient'] == 'other@example.invalid'
    finally:
        close_system(h, controller, window)


def test_protected_contact_edit_restart_delete_and_invalid_inputs(tmp_path):
    path = tmp_path / 'contacts.bin'
    store = ContactService(path)
    start = store.inventory()
    current = store.save(None, 'João', 'JOAO@example.invalid', '+55 (11) 99999-9999', start['revision'])
    row = current['rows'][0]
    assert b'joao@example.invalid' not in path.read_bytes()
    assert ContactService(path).inventory() == current
    for name, email, phone in [('', 'x@example.invalid', ''), ('João', 'bad\nBcc: x', ''), ('João', '', '11999999999')]:
        with pytest.raises(ValueError):
            store.save(None, name, email, phone, current['revision'])
    with pytest.raises(ValueError):
        store.save(None, 'Outro João', row['email'], '', current['revision'])
    with pytest.raises(PermissionError):
        store.save(row['id'], 'Mudança antiga', row['email'], row['whatsapp'], start['revision'])
    with pytest.raises(PermissionError):
        store.delete(row['id'], start['revision'])
    assert store.inventory() == current
    current = store.save(row['id'], 'João atualizado', row['email'], row['whatsapp'], current['revision'])
    assert store.delete(row['id'], current['revision'])['rows'] == []
    path.write_bytes(b'not protected')
    with pytest.raises(PermissionError):
        store.inventory()
