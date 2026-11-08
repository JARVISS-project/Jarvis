from threading import Event
from uuid import uuid4

import pytest

from jarvis.application.administration import AdministrationController
from jarvis.application.runtime_control import RuntimeControl
from tests.integration.test_administration_23bh import build


class Owner:
    pass


@pytest.mark.parametrize('mode', ['cancel', 'expired', 'foreign', 'disconnected', 'closed', 'replaced'])
def test_unconfirmed_stale_or_foreign_requests_have_zero_effect(mode):
    now, calls = [100.0], []
    control = RuntimeControl(lambda: calls.append(True), clock=lambda: now[0])
    owner = Owner()
    token = control.prepare(owner)['token']
    if mode == 'cancel':
        assert control.finish(token, owner, cancel=True) == {'cancelled': True}
    elif mode == 'expired':
        now[0] += 60
    elif mode == 'disconnected':
        control.disconnect(owner)
    elif mode == 'closed':
        control.close()
    elif mode == 'replaced':
        control.prepare(owner)
    with pytest.raises(PermissionError):
        control.finish(token, Owner() if mode == 'foreign' else owner)
    assert not calls


def test_confirmation_is_single_use_across_owners_and_uncertain_callback_is_not_retried():
    calls = []
    def uncertain():
        calls.append(True)
        raise OSError('synthetic failure')
    control = RuntimeControl(uncertain)
    first, second = Owner(), Owner()
    token = control.prepare(first)['token']
    other = control.prepare(second)['token']
    with pytest.raises(OSError):
        control.finish(token, first)
    for saved, actor in ((token, first), (other, second)):
        with pytest.raises(PermissionError):
            control.finish(saved, actor)
    with pytest.raises(PermissionError):
        control.prepare(first)
    assert calls == [True]


def test_pending_confirmations_are_bounded_and_require_an_owner():
    control = RuntimeControl(lambda: pytest.fail('stop'))
    with pytest.raises(PermissionError):
        control.prepare(None)
    owners = [Owner() for _ in range(17)]
    for owner in owners[:16]:
        control.prepare(owner)
    with pytest.raises(PermissionError):
        control.prepare(owners[-1])


@pytest.mark.parametrize('mode', ['unauthenticated', 'wrong_generation', 'extra_target', 'no_prepare', 'valid'])
def test_authenticated_administration_requires_prepared_bound_operation(tmp_path, mode):
    harness, service = build(tmp_path)
    calls = []
    service.runtime_control = RuntimeControl(lambda: calls.append(True))
    controller = AdministrationController(service, uuid4().hex)
    owner = Owner()

    def submit(method, arguments, *, generation=None, authorization=None):
        replies, done = [], Event()
        def reply(event):
            replies.append(event)
            done.set()
        payload = dict(command='admin_request', generation=generation or controller.generation,
                       interaction=uuid4().hex, method=method, arguments=arguments,
                       authorization=authorization or controller.authorization)
        controller.handle(payload, reply, owner=owner)
        assert done.wait(2)
        return replies[0]

    try:
        if mode == 'no_prepare':
            result = submit('runtime.stop', {'token': '0' * 32})
        else:
            prepared = submit('runtime.prepare_stop', {})
            assert prepared['status'] == 'done' and not calls
            token = prepared['data']['token']
            arguments = {'token': token}
            if mode == 'extra_target':
                arguments['pid'] = 123
            result = submit('runtime.stop', arguments,
                generation=uuid4().hex if mode == 'wrong_generation' else None,
                authorization='0' * 64 if mode == 'unauthenticated' else None)
        assert result['status'] == ('done' if mode == 'valid' else 'error')
        assert calls == ([True] if mode == 'valid' else [])
        assert service.registry.get('runtime.stop') is None
        if mode == 'valid':
            assert result['data'] == {'requested': True}
            assert submit('runtime.stop', {'token': token})['status'] == 'error'
            audit = (tmp_path / 'audit.log').read_text()
            assert 'runtime_stop_requested' in audit and token not in audit
    finally:
        controller.close()
        harness.close()


def test_disconnect_and_controller_close_invalidate_shutdown_confirmation(tmp_path):
    harness, service = build(tmp_path)
    service.runtime_control = RuntimeControl(lambda: pytest.fail('stop'))
    controller = AdministrationController(service, uuid4().hex)
    owner = Owner()
    token = service.runtime_control.prepare(owner)['token']
    controller.disconnect(owner)
    try:
        with pytest.raises(PermissionError):
            service.runtime_control.finish(token, owner)
    finally:
        controller.close()
        harness.close()
    with pytest.raises(PermissionError):
        service.runtime_control.prepare(owner)
