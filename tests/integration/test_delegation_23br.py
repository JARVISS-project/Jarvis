import json
import time
from threading import Event, Thread
from uuid import uuid4

import httpx
import pytest

from jarvis.application.delegation import DelegationService
from jarvis.application.text_interaction import TextInteractionController
from jarvis.integrations.text_providers import TextProviderAdapter, parse_response, request_spec
from tests.integration.test_administration_23bh import build, change, draft


class Protector:
    def protect(self, raw):
        return b'protected-test:' + raw

    def unprotect(self, raw):
        assert raw.startswith(b'protected-test:')
        return raw[len(b'protected-test:'):]


def answer(provider, text='Neutral response <script>do not execute</script>'):
    if provider == 'openai':
        return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': text}]}]}
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': text}]}}]}


def configured(tmp_path, provider='openai', handler=None, allowed=True):
    h, admin = build(tmp_path)
    calls = []
    def handle(request):
        calls.append(request)
        return handler(request) if handler else httpx.Response(200, json=answer(provider))
    service = DelegationService(h, Protector(), TextProviderAdapter(httpx.MockTransport(handle)))
    h.delegation = admin.delegation = service
    h.tool_executor.capability_policy.administrative = admin.permissions
    model = service.inventory()['providers'][0 if provider == 'openai' else 1]['models'][0]
    service.configure(provider, model, 4096, 128, 5, 'neutral-api-key-fixture')
    if allowed:
        for resource in ('model.external', 'delegation.execute'):
            change(admin, resource, 'allow')
    return h, admin, service, calls


def approve(h):
    context = h.current_confirmation_context()
    assert context
    return h.processar_mensagem('sim', manage_runtime=False,
        confirmation_session_id=context[0], confirmation_challenge_id=context[1])


def test_openai_vertical_config_preview_executor_bound_approval_and_privacy(tmp_path):
    h, admin, s, calls = configured(tmp_path)
    admin.save_memory(draft('PRIVATE-MEMORY-MARKER'))
    h.memory.add_user_message('PRIVATE-HISTORY-MARKER')
    original = h.memory.get_history()
    preview = s.prepare('openai', 'Explain gravity simply.')
    assert preview['state'] == 'prepared' and 'Nenhum' in preview['text'] and 'USD' in preview['text']
    assert not calls and 'key' not in json.dumps(s.inventory())
    assert 'aprovação' in s.submit(preview['token']) and not calls
    context = h.current_confirmation_context()
    assert 'concluída' in approve(h)
    assert len(calls) == 1
    sent = json.loads(calls[0].content)
    assert sent['input'] == 'Explain gravity simply.' and sent['store'] is False
    assert sent['max_output_tokens'] == 128 and sent['tools'] == [] and sent['tool_choice'] == 'none'
    assert h.memory.get_history() == original
    assert 'PRIVATE' not in json.dumps(sent)
    assert 'concluída' not in s.submit(preview['token']) and len(calls) == 1
    h.processar_mensagem('sim', manage_runtime=False, confirmation_session_id=context[0], confirmation_challenge_id=context[1])
    assert len(calls) == 1
    persisted = s.journal.read_text() + (tmp_path / 'audit.log').read_text()
    assert not any(x in persisted for x in ('Explain gravity', 'PRIVATE', 'neutral-api-key-fixture', '<script>'))


def test_gemini_same_contract_official_endpoint_headers_limits(tmp_path):
    h, admin, s, calls = configured(tmp_path, 'gemini')
    h.processar_mensagem('delegar para gemini: Explain gravity simply.', manage_runtime=False)
    assert not calls
    assert 'concluída' in approve(h)
    request = calls[0]
    assert str(request.url) == 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent'
    assert request.headers['x-goog-api-key'] == 'neutral-api-key-fixture'
    assert 'key=' not in str(request.url)
    body = json.loads(request.content)
    assert body['generationConfig']['maxOutputTokens'] == 128 and 'tools' not in body


@pytest.mark.parametrize('resource', ['model.external', 'delegation.execute', 'tool:external_text_delegate'])
def test_blocked_permissions_zero_send(tmp_path, resource):
    h, admin, s, calls = configured(tmp_path)
    preview = s.prepare('openai', 'Neutral question')
    change(admin, resource, 'block') if resource != 'tool:external_text_delegate' else admin.permissions.set_rule(
        resource, 'use', 'block', expected_revision=admin.permissions.read()[1])
    s.submit(preview['token'])
    assert not calls


def test_defaults_block_and_missing_credentials_unavailable(tmp_path):
    h, admin, s, calls = configured(tmp_path, allowed=False)
    with pytest.raises(PermissionError):
        s.prepare('openai', 'Neutral task')
    with pytest.raises(PermissionError):
        s.prepare('gemini', 'Neutral task')
    assert not calls


def test_denied_confirmation_discards_preview_and_cannot_replay(tmp_path):
    h, admin, s, calls = configured(tmp_path)
    token = s.prepare('openai', 'Neutral task')['token']
    s.submit(token)
    context = h.current_confirmation_context()
    h.processar_mensagem('cancelar', manage_runtime=False,
                         confirmation_session_id=context[0], confirmation_challenge_id=context[1])
    assert token not in s.drafts
    assert 'nenhum envio repetido' in s.submit(token) and not calls


@pytest.mark.parametrize('mutation', ['payload', 'provider', 'limit', 'revoke', 'edit', 'cancel'])
def test_pending_binding_invalidated_zero_send(tmp_path, mutation):
    h, admin, s, calls = configured(tmp_path)
    prepared = s.prepare('openai', 'Neutral task')
    s.submit(prepared['token'])
    context = h.current_confirmation_context()
    if mutation == 'payload':
        s.drafts[prepared['token']]['payload'] = 'Adulterated task'
    elif mutation == 'provider':
        s.drafts[prepared['token']]['provider'] = 'gemini'
    elif mutation == 'limit':
        s.drafts[prepared['token']]['config']['output_limit'] = 512
    elif mutation == 'revoke':
        change(admin, 'delegation.execute', 'block')
    elif mutation == 'edit':
        s.configure('openai', 'gpt-4.1-mini', 4096, 128, 5, '')
    else:
        s.invalidate()
    h.processar_mensagem('sim', manage_runtime=False, confirmation_session_id=context[0], confirmation_challenge_id=context[1])
    assert not calls


@pytest.mark.parametrize('status, message', [(401, 'Credencial rejeitada'), (403, 'não autorizado'), (429, 'Limite'), (500, 'indisponível')])
def test_http_failures_sanitized_no_retry(tmp_path, status, message):
    h, admin, s, calls = configured(tmp_path, handler=lambda r: httpx.Response(status, text='PRIVATE API KEY secret'))
    s.submit(s.prepare('openai', 'Neutral task')['token'])
    result = approve(h)
    assert message in result and 'PRIVATE' not in result and len(calls) == 1
    assert s.records[-1]['state'] == 'failed'


def test_timeout_partial_malformed_responses_no_success_or_retry(tmp_path):
    def failure(request):
        raise httpx.ReadTimeout('PRIVATE URL KEY', request=request)
    h, admin, s, calls = configured(tmp_path, handler=failure)
    s.submit(s.prepare('openai', 'Neutral task')['token'])
    result = approve(h)
    assert 'Tempo limite' in result and 'PRIVATE' not in result and len(calls) == 1
    for provider, raw in [('openai', {'status': 'incomplete'}), ('gemini', {'candidates': [{'finishReason': 'MAX_TOKENS'}]})]:
        with pytest.raises(RuntimeError, match='parcial'):
            parse_response(provider, raw)


@pytest.mark.parametrize('kind', ['partial', 'remote_tool', 'secret', 'redirect', 'oversized'])
def test_response_validation_no_unsafe_success_or_second_send(tmp_path, kind):
    def response(request):
        if kind == 'redirect':
            return httpx.Response(302, headers={'location': 'https://example.invalid/credential-steal'})
        if kind == 'oversized':
            return httpx.Response(200, content=b'x' * 262145)
        value = answer('openai', 'neutral-api-key-fixture' if kind == 'secret' else 'Neutral text')
        if kind == 'partial':
            value['status'] = 'incomplete'
        if kind == 'remote_tool':
            value['output'] = [{'type': 'function_call', 'name': 'shell', 'arguments': 'arbitrary code'}]
        return httpx.Response(200, json=value)
    h, admin, s, calls = configured(tmp_path, handler=response)
    s.submit(s.prepare('openai', 'Neutral task')['token'])
    result = approve(h)
    assert 'concluída' not in result and 'neutral-api-key-fixture' not in result
    assert len(calls) == 1 and s.records[-1]['state'] == 'failed'


def test_interrupted_journal_restart_and_new_preview_invalidates_old(tmp_path):
    from jarvis.remote.auth import _atomic_write_json
    h, admin, s, calls = configured(tmp_path)
    old = s.prepare('openai', 'Neutral first task')['token']
    newer = s.prepare('openai', 'Neutral second task')['token']
    assert old not in s.drafts and newer in s.drafts
    assert 'nenhum envio repetido' in s.submit(old) and not calls
    _atomic_write_json(s.journal, [{'id': newer, 'state': 'sending'}])
    restarted = DelegationService(h, Protector(), s.adapter)
    assert restarted.records == [{'id': newer, 'state': 'failed'}]
    assert 'nenhum envio repetido' in restarted.submit(newer) and not calls


def test_total_timeout_and_cancel_before_network(tmp_path):
    calls, release = [], Event()
    def stalled(request):
        calls.append(request)
        release.wait(2)
        return httpx.Response(200, json=answer('openai'))
    adapter = TextProviderAdapter(httpx.MockTransport(stalled))
    cancelled = Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match='antes'):
        adapter.generate('openai', 'gpt-4.1-mini', 'Neutral task', 128, .1, 'fake', cancelled)
    assert not calls
    started = time.monotonic()
    with pytest.raises(RuntimeError, match='Tempo limite'):
        adapter.generate('openai', 'gpt-4.1-mini', 'Neutral task', 128, .1, 'fake', Event())
    assert time.monotonic() - started < .8 and len(calls) == 1
    release.set()


def test_input_budget_secret_context_model_and_direct_execution_denied(tmp_path):
    h, admin, s, calls = configured(tmp_path)
    for task in ('x' * 4096, 'neutral-api-key-fixture', 'Bearer private-token',
                 '{"access_token":"neutral-fixture-token"}', 'refresh_token = neutral',
                 'client_secret: neutral', 'ya29.neutralfixture'):
        with pytest.raises((ValueError, PermissionError)):
            s.prepare('openai', task)
    prepared = s.prepare('openai', 'Neutral task', 'Explicitly selected neutral text')
    assert 'Explicitly selected neutral text' in prepared['text']
    preparation = s.tool.prepare(prepared['token'])
    with pytest.raises(PermissionError):
        s.tool.execute_authorized(preparation)
    with pytest.raises(PermissionError):
        s.tool.execute(token=prepared['token'])
    with pytest.raises(PermissionError):
        s.tool.bind_authorized(preparation, object())
    with pytest.raises(ValueError):
        s.configure('gemini', 'https://evil.example/model', 4096, 128, 5, 'fake')
    assert not calls


def test_cancel_inflight_concurrency_duplicates_and_restart_no_resend(tmp_path):
    entered, release = Event(), Event()
    def wait(request):
        entered.set()
        release.wait(3)
        return httpx.Response(200, json=answer('openai'))
    h, admin, s, calls = configured(tmp_path, handler=wait)
    token = s.prepare('openai', 'Neutral task')['token']
    s.submit(token)
    out = []
    worker = Thread(target=lambda: out.append(approve(h)))
    worker.start()
    assert entered.wait(2)
    started = time.monotonic()
    assert 'nenhum envio repetido' in s.submit(token)
    assert time.monotonic() - started < 0.5 and len(calls) == 1
    s.cancel()
    worker.join(2)
    assert not worker.is_alive() and len(calls) == 1 and 'concluída' not in out[0]
    release.set()
    restarted = DelegationService(h, Protector(), s.adapter)
    assert restarted.records[-1]['state'] == 'cancelled' and not restarted.drafts
    assert 'nenhum envio repetido' in restarted.submit(token) and len(calls) == 1


def test_real_dpapi_temporary_credential_persistence_no_network(tmp_path):
    h, admin = build(tmp_path)
    s = h.delegation
    s.configure('openai', 'gpt-4.1-mini', 2048, 128, 10, 'neutral-not-a-real-key')
    assert b'neutral-not-a-real-key' not in s.path.read_bytes()
    restarted = DelegationService(h)
    assert restarted.read()['openai']['key'] == 'neutral-not-a-real-key'
    assert 'neutral-not-a-real-key' not in json.dumps(restarted.inventory())
    assert s.path.resolve() in admin.permissions.protected_paths


def test_text_controller_e2e_confirmation_response_progress_and_duplicate(tmp_path):
    h, admin, s, calls = configured(tmp_path)
    controller = TextInteractionController(h)
    events, done = [], Event()
    def reply(event):
        events.append(event)
        if event['status'] in {'confirmation', 'done', 'error'}:
            done.set()
    request = {'command': 'text_submit', 'generation': controller.generation,
               'interaction': uuid4().hex, 'text': 'delegar openai: Neutral task'}
    try:
        controller.handle('owner', request, reply)
        assert done.wait(3) and not calls and events[-1]['status'] == 'confirmation'
        context = events[-1]
        done.clear()
        confirmation = {'command': 'text_confirm', 'generation': controller.generation,
            'interaction': request['interaction'], 'session': context['session'], 'challenge': context['challenge']}
        controller.handle('owner', confirmation, reply)
        assert done.wait(3) and len(calls) == 1 and 'concluída' in events[-1]['text']
        assert any('enviando' in e.get('text', '') for e in events)
        controller.handle('owner', confirmation, reply)
        assert len(calls) == 1 and events[-1]['status'] == 'error'
    finally:
        controller.close()
