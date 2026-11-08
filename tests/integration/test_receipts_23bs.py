"""Sanitized API receipts, with all paid boundaries replaced by MockTransport."""
from threading import Event

import httpx
import pytest

from jarvis.integrations.text_providers import MODELS, TextProviderAdapter, response_receipt
from tests.integration.test_delegation_23br import answer, approve, configured


@pytest.mark.parametrize('provider', ['openai', 'gemini'])
def test_actual_model_and_usage_reach_executor_response_without_persistence(tmp_path, provider):
    model = MODELS[provider][0]
    value = answer(provider, 'connection confirmed')
    if provider == 'openai':
        value.update(model=model, usage=dict(input_tokens=10, output_tokens=3, total_tokens=13))
    else:
        value.update(modelVersion=model, usageMetadata=dict(promptTokenCount=10,
                     candidatesTokenCount=3, totalTokenCount=13, thoughtsTokenCount=0))
    h, admin, service, calls = configured(tmp_path, provider, lambda r: httpx.Response(200, json=value))
    try:
        preview = service.prepare(provider, 'Responda somente: conexão confirmada.')
        assert not calls
        service.submit(preview['token'])
        response = approve(h)
        assert 'connection confirmed' in response and model in response and '13' in response
        assert len(calls) == 1
        assert service.adapter.last_receipt['model'] == model
        persisted = service.journal.read_text() + (tmp_path / 'audit.log').read_text()
        assert 'connection confirmed' not in persisted and 'total_tokens' not in persisted
        service.submit(preview['token'])
        assert len(calls) == 1
    finally:
        h.close()


@pytest.mark.parametrize('value', [None, {}, {'total_tokens': True}, {'total_tokens': -1},
                                 {'total_tokens': 2147483648}, {'total_tokens': 'SECRET'}])
def test_invalid_metadata_never_becomes_evidence(value):
    receipt = response_receipt('openai', dict(model='sk-private-secret-123456789', usage=value))
    assert receipt == {'model': None, 'usage': {}}


def test_missing_receipt_is_explicitly_unverified(tmp_path):
    h, admin, service, calls = configured(tmp_path)
    try:
        service.submit(service.prepare('openai', 'neutral task')['token'])
        response = approve(h)
        assert 'não verificado' in response and len(calls) == 1
    finally:
        h.close()


def test_failure_clears_previous_receipt_without_retry():
    count = []
    value = answer('openai', 'neutral')
    value.update(model=MODELS['openai'][0], usage={'total_tokens': 1})
    def handle(request):
        count.append(request)
        return httpx.Response(200, json=value) if len(count) == 1 else httpx.Response(429)
    adapter = TextProviderAdapter(httpx.MockTransport(handle))
    arguments = ('openai', MODELS['openai'][0], 'neutral', 16, 5, 'fixture')
    adapter.generate(*arguments, Event())
    assert adapter.last_receipt is not None
    with pytest.raises(RuntimeError, match='retry'):
        adapter.generate(*arguments, Event())
    assert adapter.last_receipt is None and len(count) == 2


def test_google_unavailable_does_not_label_api_configuration_blocked(tmp_path):
    from PySide6.QtWidgets import QTabWidget
    from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle
    app, h, admin, controller, window = window_system(tmp_path)
    try:
        page = window.accounts_page
        page.refresh()
        settle(app, lambda: not page.busy and not page.delegation.busy)
        # Temporary profile has no Google account; the Google error remains truthful.
        assert 'bloqueada' in page.status.text()
        tabs = page.findChild(QTabWidget)
        tabs.setCurrentIndex(2)
        assert page.status.isHidden()
        assert 'configure chave' in page.delegation.availability.text()
        assert page.delegation.save_button.isEnabled()
        tabs.setCurrentIndex(0)
        assert not page.status.isHidden() and 'bloqueada' in page.status.text()
    finally:
        close_system(h, controller, window)
