"""Real administrative worker: typed consent failures, no private error echo."""
from threading import Event
from uuid import uuid4

import pytest

from jarvis.application.administration import AdministrationController
from jarvis.integrations.google.errors import (
    GoogleAuthorizationCancelledError, GoogleAuthorizationDeniedError,
    GoogleAuthorizationTimeoutError, GoogleBrowserLaunchError, GoogleScopeError,
)
from tests.integration.test_administration_23bh import build


@pytest.mark.parametrize('method, error_type, expected', [
    ('accounts.connect', GoogleAuthorizationTimeoutError, 'prazo do login Google expirou'),
    ('accounts.connect', GoogleAuthorizationCancelledError, 'Login Google cancelado'),
    ('accounts.connect', GoogleAuthorizationDeniedError, 'autorização Google foi recusada'),
    ('accounts.connect', GoogleBrowserLaunchError, 'abrir o navegador'),
    ('accounts.connect', GoogleScopeError, 'Dados inválidos ou serviço indisponível'),
    ('accounts.connect', RuntimeError, 'Dados inválidos ou serviço indisponível'),
    ('accounts.connect', PermissionError, 'Operação bloqueada'),
    ('memory.list', GoogleAuthorizationTimeoutError, 'Dados inválidos ou serviço indisponível'),
])
def test_worker_sends_actionable_consent_failure_without_private_details(tmp_path, monkeypatch, method, error_type, expected):
    def dispatch(*args, **kwargs):
        raise error_type('private-exception-body-and-callback-values')
    harness, service = build(tmp_path)
    monkeypatch.setattr(service, 'dispatch', dispatch)
    controller = AdministrationController(service, uuid4().hex)
    complete, replies = Event(), []
    def reply(event):
        replies.append(event)
        complete.set()
    try:
        controller.handle({'command': 'admin_request', 'authorization': controller.authorization,
            'generation': controller.generation, 'interaction': uuid4().hex,
            'method': method, 'arguments': {}}, reply)
        assert complete.wait(2)
        assert len(replies) == 1 and replies[0]['status'] == 'error'
        assert expected in replies[0]['error']
        assert 'private-exception' not in repr(replies)
        assert 'data' not in replies[0]
    finally:
        controller.close()
        harness.close_confirmation_sessions()
