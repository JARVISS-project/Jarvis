"""Calendar background workflow, real receipt/executor, simulated effects only."""
from copy import deepcopy
from datetime import timedelta

import pytest

from jarvis.application.calendar_workflow import CalendarWorkflow, digest
from jarvis.integrations.contact_messaging import MessageRejectedError
from tests.application.test_calendar_assistant import setup_context
from tests.integration.test_calendar_read_23ba import NOW
from tests.integration.test_administration_23bh import change
from tests.ui.test_administrative_widgets_23bh import close_system
from tests.ui.test_administrative_widgets_23bh import settle


class Messages:
    def __init__(self, state='handed_off'):
        self.calls = []
        self.state = state
    def send(self, draft, smtp):
        self.calls.append((deepcopy(draft), deepcopy(smtp)))
        if self.state == 'result_unknown':
            raise TimeoutError('private-email secret-password')
        if self.state == 'rejected':
            raise MessageRejectedError('private response')
        return {'state': self.state, 'message': 'Recibo simulado', 'receipt': 'opaque'}


def system(tmp_path):
    values, _args = setup_context(tmp_path)
    workflow = values[2].workflow
    workflow.clock = lambda: NOW
    workflow.next_scan = NOW
    workflow.adapter = Messages()
    return values, workflow


def row(workflow):
    return workflow.inventory()['rows'][0]


def settings(workflow, **changes):
    state = workflow.inventory()
    return workflow.settings({**state['settings'], **changes}, state['revision'])


def prepare(workflow, actor, channel='auto', body=''):
    item = row(workflow)
    return workflow.prepare(item['id'], item['revision'], channel, '', body, actor=actor)


def smtp(workflow):
    workflow.save_channels({'host': 'smtp.example.invalid', 'port': 465, 'username': 'sender@example.invalid',
        'sender': 'sender@example.invalid', 'password': 'synthetic-password'}, workflow.channels()['revision'])


def test_default_no_background_reads_and_opt_in_changed_only_proposals(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, boundary = values
    try:
        calls = len(boundary.calls)
        workflow.tick()
        assert len(boundary.calls) == calls
        settings(workflow, enabled=True)
        workflow.tick()
        item = row(workflow)
        assert item['draft']['channel'] == 'whatsapp' and item['contact'] == 'João'
        assert not workflow.adapter.calls
        revision = item['revision']
        workflow.scan()
        assert row(workflow)['revision'] == revision and len(workflow.inventory()['rows']) == 1
        boundary.items[0]['summary'] = 'Horário ou assunto alterado'
        workflow.scan()
        assert row(workflow)['changed'] and row(workflow)['state'] == 'review'
        assert not workflow.adapter.calls
    finally:
        close_system(h, controller, window)


def test_snooze_ignore_quiet_pause_and_restart_without_duplicate(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, boundary = values
    try:
        workflow.scan()
        item = row(workflow)
        workflow.action(item['id'], item['revision'], 'snooze')
        assert not workflow.inventory()['rows']
        workflow.clock = lambda: NOW + timedelta(hours=1, seconds=1)
        workflow.scan()
        item = row(workflow)
        assert item['state'] == 'pending'
        workflow.action(item['id'], item['revision'], 'ignore')
        workflow.scan()
        assert not workflow.inventory()['rows']
        restored = CalendarWorkflow(service, clock=workflow.clock, adapter=workflow.adapter)
        restored.scan()
        assert not restored.inventory()['rows']
        settings(workflow, enabled=True, quiet_start=0, quiet_end=23)
        calls = len(boundary.calls)
        workflow.next_scan = NOW
        workflow.tick()
        assert len(boundary.calls) == calls
        settings(workflow, enabled=False)
        workflow.tick()
        assert len(boundary.calls) == calls
    finally:
        close_system(h, controller, window)


def test_message_approval_bound_to_owner_content_and_single_use_receipt(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, boundary = values
    owner = object()
    try:
        workflow.scan()
        preview = prepare(workflow, owner, body='Texto revisado pelo usuário.')
        with pytest.raises(PermissionError):
            workflow.approve(preview['token'], actor=object())
        with pytest.raises(PermissionError):
            workflow.tool.execute(token=preview['token'])
        with pytest.raises(PermissionError):
            workflow.tool.bind_authorized(workflow.tool.prepare(preview['token']))
        assert not workflow.adapter.calls
        result = workflow.approve(preview['token'], actor=owner)
        assert result['status'] == 'success' and len(workflow.adapter.calls) == 1
        assert workflow.adapter.calls[0][0]['body'] == 'Texto revisado pelo usuário.'
        assert row(workflow)['state'] == 'handed_off'
        with pytest.raises(PermissionError):
            workflow.approve(preview['token'], actor=owner)
        with pytest.raises(PermissionError):
            prepare(workflow, owner)
        assert 'contact_message_send' not in {tool.name for tool in h.tool_registry.list_tools()}
        audit = (tmp_path / 'audit.log').read_text()
        assert 'contact_message_send' in audit and 'João' not in audit and '999999' not in audit
    finally:
        close_system(h, controller, window)


@pytest.mark.parametrize('case', ['cancel', 'expire', 'contact', 'event', 'policy', 'disconnect'])
def test_stale_cancelled_or_revoked_preview_never_sends(tmp_path, case):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, boundary = values
    owner = object()
    try:
        workflow.scan()
        preview = prepare(workflow, owner)
        if case == 'cancel':
            workflow.cancel(preview['token'], actor=owner)
        elif case == 'expire':
            workflow.clock = lambda: NOW + timedelta(minutes=3)
        elif case == 'contact':
            inventory = service.contacts.inventory()
            contact = inventory['rows'][0]
            service.contacts.save(contact['id'], contact['name'], contact['email'], '+5511888888888', inventory['revision'])
        elif case == 'event':
            boundary.items[0]['summary'] = 'Evento mudou'
        elif case == 'policy':
            change(service, 'tool:contact_message_send', 'block')
        else:
            workflow.disconnect(owner)
        with pytest.raises(PermissionError):
            workflow.approve(preview['token'], actor=owner)
        assert workflow.adapter.calls == []
    finally:
        close_system(h, controller, window)


def test_unknown_result_survives_restart_and_history_trimming_without_retry(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, _ = values
    owner = object()
    workflow.adapter.state = 'result_unknown'
    try:
        workflow.scan()
        preview = prepare(workflow, owner)
        result = workflow.approve(preview['token'], actor=owner)
        assert result['status'] == 'error' and len(workflow.adapter.calls) == 1
        assert row(workflow)['state'] == 'result_unknown'
        assert 'secret-password' not in str(result)
        workflow.data['history'] = []
        workflow._save()
        restored = CalendarWorkflow(service, clock=lambda: NOW, adapter=workflow.adapter)
        restored.bind_executor(h.tool_executor.safety_manager, h.tool_executor.audit_logger)
        restored.scan()
        with pytest.raises(PermissionError):
            prepare(restored, owner)
        assert len(workflow.adapter.calls) == 1
        item = row(restored)
        restored.action(item['id'], item['revision'], 'not_sent')
        assert row(restored)['state'] == 'pending'
    finally:
        close_system(h, controller, window)


def test_authorized_rule_smtp_once_and_whatsapp_never_opens_in_background(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, _ = values
    workflow.adapter.state = 'accepted'
    try:
        smtp(workflow)
        contact = service.contacts.inventory()['rows'][0]
        rule = {'id': 'a' * 32, 'enabled': True, 'contact_key': 'local:' + contact['id'], 'purpose': 'confirm',
                'channel': 'auto', 'lead_minutes': 1440, 'daily_limit': 1, 'start_hour': 9, 'end_hour': 18}
        with pytest.raises(PermissionError):
            workflow.save_rule(rule, workflow.data['revision'], False)
        workflow.save_rule(rule, workflow.data['revision'], True)
        settings(workflow, enabled=True, mode='rules')
        workflow.scan()
        assert workflow.adapter.calls == []  # WhatsApp first is a manual handoff.
        rule['channel'] = 'email'
        workflow.save_rule(rule, workflow.data['revision'], True)
        workflow.scan()
        assert len(workflow.adapter.calls) == 1 and workflow.adapter.calls[0][0]['channel'] == 'email'
        workflow.scan()
        assert len(workflow.adapter.calls) == 1
        assert workflow.inventory()['history'][-1]['state'] == 'accepted'
    finally:
        close_system(h, controller, window)


def test_owned_admin_worker_monitors_without_ui_scan_or_extra_thread(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, boundary = values
    try:
        settings(workflow, enabled=True)
        settle(app, lambda: workflow.data['last_scan'] is not None)
        assert workflow.data['proposals'] and not workflow.adapter.calls
    finally:
        close_system(h, controller, window)


@pytest.mark.parametrize('case', ['contact', 'sender', 'template', 'calendar', 'policy', 'quiet', 'limit'])
def test_rule_approval_scope_and_daily_limits_are_enforced(tmp_path, case, monkeypatch):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, _ = values
    workflow.adapter.state = 'accepted'
    try:
        with service._lock:
            smtp(workflow)
            workflow.scan()
            contact = service.contacts.inventory()['rows'][0]
            rule = {'id': 'b' * 32, 'enabled': True, 'contact_key': 'local:' + contact['id'], 'purpose': 'confirm',
                    'channel': 'email', 'lead_minutes': 1440, 'daily_limit': 1, 'start_hour': 9, 'end_hour': 18}
            workflow.save_rule(rule, workflow.data['revision'], True)
            settings(workflow, enabled=True, mode='rules')
            if case == 'contact':
                service.contacts.save(contact['id'], contact['name'], contact['email'], '+5511888888888', service.contacts.inventory()['revision'])
            elif case == 'sender':
                state = workflow.channel_store.read({})['smtp']
                workflow.save_channels({**state, 'sender': 'other@example.invalid'}, workflow.channels()['revision'])
            elif case == 'template':
                from jarvis.application.calendar_assistant import MESSAGE_TEMPLATES
                monkeypatch.setitem(MESSAGE_TEMPLATES, 'confirm', MESSAGE_TEMPLATES['confirm'] + ' Novo modelo.')
            elif case == 'policy':
                change(service, 'tool:contact_message_send', 'ask')
            elif case == 'calendar':
                settings(workflow, alias='trabalho')
            elif case == 'quiet':
                settings(workflow, quiet_start=0, quiet_end=23)
            else:
                workflow.data['attempts'] = {str(i): {'state': 'accepted', 'at': NOW.isoformat(), 'contact_key': 'other'} for i in range(5)}
            workflow.scan()
            assert not workflow.adapter.calls
            if case in {'contact', 'sender', 'template', 'calendar'}:
                assert workflow.inventory()['rules'][0]['requires_review']
    finally:
        close_system(h, controller, window)
