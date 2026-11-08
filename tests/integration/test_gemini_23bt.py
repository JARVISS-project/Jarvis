"""Offline generateContent contract; synthetic signatures, no external calls."""
from copy import deepcopy
import json
from threading import Event

import httpx
import pytest

from jarvis.integrations.text_providers import (
    MODELS, ProviderResponseError, TextProviderAdapter, parse_response,
)
from tests.integration.test_delegation_23br import approve, configured


@pytest.fixture
def response():
    return {
        'candidates': [{
            'content': {'role': 'model', 'parts': [
                {'text': 'conexao confirmada', 'thoughtSignature': 'c3ludGhldGlj'}]},
            'finishReason': 'STOP', 'index': 0,
            'safetyRatings': [{'category': 'HARM_CATEGORY_DANGEROUS_CONTENT',
                               'probability': 'NEGLIGIBLE', 'blocked': False}],
        }],
        'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 3,
                          'totalTokenCount': 13, 'thoughtsTokenCount': 0},
        'modelVersion': MODELS['gemini'][0], 'responseId': 'synthetic-response-id',
    }


@pytest.mark.parametrize('signed', [False, True])
def test_text_signature_is_compatible_and_discarded(response, signed):
    part = response['candidates'][0]['content']['parts'][0]
    if not signed:
        part.pop('thoughtSignature')
    assert parse_response('gemini', response) == 'conexao confirmada'


def test_empty_signed_trailer_does_not_erase_text(response):
    parts = response['candidates'][0]['content']['parts']
    parts[0].pop('thoughtSignature')
    parts.append({'text': '', 'thoughtSignature': 'c3ludGhldGlj'})
    assert parse_response('gemini', response).strip() == 'conexao confirmada'


@pytest.mark.parametrize('finish, reason', [
    ('MAX_TOKENS', 'incomplete'), ('SAFETY', 'safety'), ('RECITATION', 'safety'),
    ('SPII', 'safety'), ('PROHIBITED_CONTENT', 'safety'), ('BLOCKLIST', 'safety'),
    ('MALFORMED_FUNCTION_CALL', 'incomplete'), ('PRIVATE-SECRET', 'incomplete'),
    (None, 'incomplete'),
])
def test_finish_rejections_sanitized(response, finish, reason):
    response['candidates'][0]['finishReason'] = finish
    with pytest.raises(ProviderResponseError) as raised:
        parse_response('gemini', response)
    assert raised.value.reason == reason
    assert 'HTTP=200' in str(raised.value)
    assert 'PRIVATE' not in str(raised.value) and 'c3ludGhldGlj' not in str(raised.value)
    if finish != 'PRIVATE-SECRET' and finish is not None:
        assert f'finishReason={finish}' in str(raised.value)


@pytest.mark.parametrize('part, reason', [
    ({'text': 'PRIVATE-THOUGHT', 'thought': True, 'thoughtSignature': 'c3ludGhldGlj'}, 'thought'),
    ({'text': 'neutral', 'functionCall': {'name': 'shell', 'args': {'command': 'PRIVATE'}}}, 'remote_tool'),
    ({'functionResponse': {'name': 'x', 'response': {}}}, 'remote_tool'),
    ({'executableCode': {'language': 'PYTHON', 'code': 'PRIVATE'}}, 'remote_tool'),
    ({'codeExecutionResult': {'output': 'PRIVATE'}}, 'remote_tool'),
    ({'toolCall': {'name': 'PRIVATE'}}, 'remote_tool'),
    ({'inlineData': {'mimeType': 'image/png', 'data': 'PRIVATE'}}, 'invalid_response'),
    ({'fileData': {'fileUri': 'PRIVATE'}}, 'invalid_response'),
    ({'text': 'neutral', 'unknown': 'PRIVATE'}, 'invalid_response'),
    ({'text': 42}, 'invalid_response'),
    ({'text': 'neutral', 'thought': 'false'}, 'invalid_response'),
    ({'text': 'neutral', 'thoughtSignature': {}}, 'invalid_response'),
    ({'text': '', 'thoughtSignature': 'c3ludGhldGlj'}, 'empty'),
    ({'text': '   '}, 'empty'),
    ({'text': 'x' * 131073}, 'invalid_response'),
])
def test_content_denied(response, part, reason):
    response['candidates'][0]['content']['parts'] = [part]
    with pytest.raises(ProviderResponseError) as raised:
        parse_response('gemini', response)
    assert raised.value.reason == reason
    assert 'PRIVATE' not in str(raised.value)


@pytest.mark.parametrize('mutation', ['prompt_block', 'candidate_block', 'empty_parts',
                                     'no_candidates', 'multiple_candidates', 'wrong_role'])
def test_safety_and_structure(response, mutation):
    if mutation == 'prompt_block':
        response['promptFeedback'] = {'blockReason': 'SAFETY', 'blockReasonMessage': 'PRIVATE'}
        response.pop('candidates')
    elif mutation == 'candidate_block':
        response['candidates'][0]['safetyRatings'][0]['blocked'] = True
    elif mutation == 'empty_parts':
        response['candidates'][0]['content']['parts'] = []
    elif mutation == 'no_candidates':
        response['candidates'] = []
    elif mutation == 'multiple_candidates':
        response['candidates'] *= 2
    else:
        response['candidates'][0]['content']['role'] = 'user'
    with pytest.raises(ProviderResponseError):
        parse_response('gemini', response)


@pytest.mark.parametrize('value', [None, [], {}, {'candidates': [None]},
    {'promptFeedback': None}, {'candidates': [{'finishReason': []}]},
    {'candidates': [{'finishReason': 'STOP', 'safetyRatings': [None]}]}])
def test_malformed_never_leaks_or_crashes(value):
    with pytest.raises(ProviderResponseError):
        parse_response('gemini', value)


@pytest.mark.parametrize('status, code, types, reason', [
    (429, 'RESOURCE_EXHAUSTED', ['QuotaFailure'], 'quota'),
    (429, 'RESOURCE_EXHAUSTED', ['QuotaFailure', 'RetryInfo'], 'quota'),
    (429, 'RESOURCE_EXHAUSTED', ['RetryInfo'], 'retry_hint'),
    (429, 'RESOURCE_EXHAUSTED', [], 'limit_unknown'),
    (429, 'PRIVATE-CODE', ['QuotaFailure'], 'limit_unknown'),
    (402, 'FAILED_PRECONDITION', [], 'credits'),
    (400, 'FAILED_PRECONDITION', [], 'http'),
    (401, 'UNAUTHENTICATED', [], 'credential'),
    (403, 'PERMISSION_DENIED', [], 'permission'),
    (503, 'UNAVAILABLE', [], 'http'),
])
def test_http_diagnostic_reaches_executor_without_payload_or_retry(tmp_path, status, code, types, reason):
    value = {'error': {'code': status, 'status': code, 'message': 'PRIVATE-SECRET',
        'details': [{'@type': 'type.googleapis.com/google.rpc.' + t,
                     'violations': [{'subject': 'PRIVATE-PROJECT', 'description': 'PRIVATE'}],
                     'retryDelay': 'PRIVATE'} for t in types]}}
    h, _, service, calls = configured(tmp_path, 'gemini', lambda r: httpx.Response(status, json=value))
    try:
        service.submit(service.prepare('gemini', 'Neutral task')['token'])
        assert not calls
        result = approve(h)
        assert f'HTTP={status}' in result and f'reason={reason}' in result
        assert 'PRIVATE' not in result and 'concluída' not in result
        assert len(calls) == 1 and service.adapter.last_receipt is None
        assert service.records[-1]['state'] == 'failed'
        persisted = service.journal.read_text() + (tmp_path / 'audit.log').read_text()
        assert 'PRIVATE' not in persisted and 'Neutral task' not in persisted
    finally:
        h.close()


def test_signed_text_through_bound_executor_no_signature_persistence(tmp_path, response):
    h, _, service, calls = configured(tmp_path, 'gemini', lambda r: httpx.Response(200, json=response))
    try:
        before = deepcopy(h.memory.get_history())
        preview = service.prepare('gemini', 'Responda somente: conexao confirmada.')
        assert 'preços pagos' in preview['text'] and 'não verifica o plano' in preview['text']
        service.submit(preview['token'])
        assert not calls
        result = approve(h)
        assert 'concluída' in result and 'conexao confirmada' in result
        assert len(calls) == 1 and h.memory.get_history() == before
        persisted = service.journal.read_text() + (tmp_path / 'audit.log').read_text()
        assert 'c3ludGhldGlj' not in result + persisted + json.dumps(service.adapter.last_receipt)
        assert 'thoughtSignature' not in json.dumps(service.adapter.last_receipt)
        service.submit(preview['token'])
        assert len(calls) == 1
    finally:
        h.close()


@pytest.mark.parametrize('body', [b'PRIVATE-not-json', b'[]', b'{"error": null}', b'', b'x' * 262145],
                         ids=['invalid-json', 'array', 'null-error', 'empty', 'oversized'])
def test_malformed_http_error_keeps_status(body):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(429, content=body)
    adapter = TextProviderAdapter(httpx.MockTransport(handle))
    with pytest.raises(ProviderResponseError, match='HTTP=429'):
        adapter.generate('gemini', MODELS['gemini'][0], 'neutral', 16, 5, 'fixture', Event())
    assert len(calls) == 1 and adapter.last_receipt is None


@pytest.mark.parametrize('kind, reason', [('partial', 'incomplete'), ('blocked', 'safety'),
                                         ('thought', 'thought'), ('tool', 'remote_tool')])
def test_rejection_diagnostic_survives_executor_and_never_succeeds(tmp_path, response, kind, reason):
    candidate = response['candidates'][0]
    if kind == 'partial':
        candidate['finishReason'] = 'MAX_TOKENS'
    elif kind == 'blocked':
        candidate['finishReason'] = 'SAFETY'
    elif kind == 'thought':
        candidate['content']['parts'].insert(0, {'text': 'PRIVATE-THOUGHT', 'thought': True})
    else:
        candidate['content']['parts'].append({'functionCall': {'name': 'PRIVATE-TOOL'}})
    h, _, service, calls = configured(tmp_path, 'gemini', lambda r: httpx.Response(200, json=response))
    try:
        service.submit(service.prepare('gemini', 'neutral')['token'])
        result = approve(h)
        assert f'reason={reason}' in result and 'HTTP=200' in result
        assert 'PRIVATE' not in result and 'c3ludGhldGlj' not in result
        assert 'concluída' not in result and len(calls) == 1
        assert service.adapter.last_receipt is None and service.records[-1]['state'] == 'failed'
    finally:
        h.close()
