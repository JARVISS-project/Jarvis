from threading import Event, RLock, enumerate as threads
from uuid import uuid4

import pytest

from jarvis.application.state import AgentStateBus
from jarvis.application.state_ipc import LocalStateClient, LocalStateServer
from jarvis.application.text_interaction import TextEventBus, TextInteractionController


class FakeRuntime:
    def __init__(self):
        self._turn_lock = RLock()
        self.calls = []
        self.context = None
        self.entered = Event()
        self.release = Event()
        self.cancelled = 0

    def current_confirmation_context(self):
        return self.context

    def cancel_pending_confirmation(self):
        self.cancelled += 1
        self.context = None

    def processar_mensagem(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if text == 'fail':
            raise RuntimeError('private details must not leave worker')
        if text == 'block':
            self.entered.set()
            assert self.release.wait(2)
        if text == 'confirm':
            self.context = (str(uuid4()), str(uuid4()))
            return 'Prévia completa'
        if text == 'sim':
            assert kwargs['confirmation_session_id'] == self.context[0]
            assert kwargs['confirmation_challenge_id'] == self.context[1]
            self.context = None
        return 'Resposta textual'


def submit(controller, text='hello', **extra):
    return {'command': 'text_submit', 'generation': controller.generation,
            'interaction': uuid4().hex, 'text': text, **extra}


def collect():
    events = []
    done = Event()
    def reply(event):
        events.append(event)
        if event['status'] not in {'busy', 'ready'}:
            done.set()
    return events, done, reply


@pytest.mark.parametrize('message,status', [('hello', 'done'), ('fail', 'error')])
def test_text_response_failure_and_cleanup(message, status):
    runtime = FakeRuntime()
    controller = TextInteractionController(runtime)
    events, done, reply = collect()
    try:
        controller.handle('owner', submit(controller, message), reply)
        assert done.wait(2)
        assert [e['status'] for e in events] == ['busy', status]
        assert 'private details' not in str(events)
        assert runtime.calls[0][1] == {'response_mode': 'text'}
    finally:
        controller.close()
    assert not any(t.name == 'jarvis-ui-text' for t in threads())


def test_duplicate_concurrency_and_cancel_inflight():
    runtime = FakeRuntime()
    controller = TextInteractionController(runtime)
    events, done, reply = collect()
    request = submit(controller, 'block')
    try:
        controller.handle('owner', request, reply)
        assert runtime.entered.wait(2)
        controller.handle('other', submit(controller), reply)
        controller.handle('owner', request, reply)
        controller.handle('owner', {k: v for k, v in request.items() if k != 'text'} | {'command': 'text_cancel'}, reply)
        runtime.release.set()
        controller.close()
        assert len(runtime.calls) == 1
        assert events[-1]['status'] == 'cancelled'
        assert 'desfeitos' in events[-1]['text']
    finally:
        runtime.release.set()
        if controller._thread.is_alive():
            controller.close()


def test_confirmation_owner_challenge_expiry_and_replay():
    runtime = FakeRuntime()
    controller = TextInteractionController(runtime)
    events, done, reply = collect()
    request = submit(controller, 'confirm')
    try:
        controller.handle('owner', request, reply)
        assert done.wait(2)
        confirmation = {k: request[k] for k in ('generation', 'interaction')}
        confirmation.update(command='text_confirm', session=runtime.context[0], challenge=runtime.context[1])
        controller.handle('other', confirmation, reply)
        assert len(runtime.calls) == 1
        controller.handle('owner', {**confirmation, 'challenge': str(uuid4())}, reply)
        assert len(runtime.calls) == 1
        done.clear()
        controller.handle('owner', confirmation, reply)
        assert done.wait(2)
        assert len(runtime.calls) == 2
        controller.handle('owner', confirmation, reply)
        assert len(runtime.calls) == 2
        # Another pending challenge expired independently in Harness.
        done.clear()
        request = submit(controller, 'confirm')
        controller.handle('owner', request, reply)
        assert done.wait(2)
        session, challenge = runtime.context
        runtime.context = None
        done.clear()
        controller.handle('owner', {'command': 'text_confirm', 'generation': controller.generation,
                                   'interaction': request['interaction'], 'session': session,
                                   'challenge': challenge}, reply)
        assert done.wait(2)
        assert events[-1]['status'] == 'error'
        assert len(runtime.calls) == 3
    finally:
        controller.close()


def test_disconnect_cancels_owned_confirmation_and_restart_fences():
    runtime = FakeRuntime()
    first = TextInteractionController(runtime)
    events, done, reply = collect()
    old = submit(first, 'confirm')
    first.handle('owner', old, reply)
    assert done.wait(2)
    first.disconnect('owner')
    first.close()
    assert runtime.context is None
    second = TextInteractionController(runtime)
    try:
        second.handle('owner', old, reply)
        assert events[-1]['status'] == 'error'
        assert len(runtime.calls) == 1
    finally:
        second.close()


def test_existing_loopback_ipc_private_text_and_no_broadcast(tmp_path):
    runtime = FakeRuntime()
    controller = TextInteractionController(runtime)
    endpoint_path = tmp_path / 'endpoint.json'
    server = LocalStateServer(AgentStateBus(), endpoint_path=endpoint_path, text_controller=controller)
    endpoint = server.start()
    bus, other_bus = TextEventBus(), TextEventBus()
    ready, other_ready, done = Event(), Event(), Event()
    events, other = [], []
    bus.subscribe(lambda e: (events.append(e), ready.set() if e.get('status') == 'ready' else None,
                             done.set() if e.get('status') == 'done' else None))
    other_bus.subscribe(lambda e: (other.append(e), other_ready.set() if e.get('status') == 'ready' else None))
    client = LocalStateClient(AgentStateBus(), endpoint, text_bus=bus, endpoint_path=endpoint_path)
    peer = LocalStateClient(AgentStateBus(), endpoint, text_bus=other_bus)
    client.start()
    peer.start()
    try:
        assert ready.wait(2) and other_ready.wait(2)
        client.send_command(submit(controller, 'private message'))
        assert done.wait(2)
        assert events[-1]['text'] == 'Resposta textual'
        assert not any(e.get('interaction') for e in other)
        assert not any('private message' in p.read_text() for p in tmp_path.iterdir())
    finally:
        client.close()
        peer.close()
        server.close()
        controller.close()


@pytest.mark.parametrize('raw', [b'{"command":"text_submit","command":"text_cancel"}',
                                b'[]', b'null', b'{', b'{}'])
def test_ipc_rejects_malformed_and_duplicate_keys(raw):
    assert LocalStateServer._decode_text_command(raw) is None


def test_client_follows_new_runtime_endpoint_without_replaying_old_message(tmp_path):
    runtime = FakeRuntime()
    path = tmp_path / 'endpoint.json'
    first = TextInteractionController(runtime)
    server = LocalStateServer(AgentStateBus(), endpoint_path=path, text_controller=first)
    bus = TextEventBus()
    ready, done = Event(), Event()
    events = []
    bus.subscribe(lambda e: (events.append(e), ready.set() if e.get('status') == 'ready' else None,
                             done.set() if e.get('status') in {'done', 'error'} else None))
    client = LocalStateClient(AgentStateBus(), server.start(), text_bus=bus,
                               endpoint_path=path, reconnect_seconds=0.05)
    client.start()
    second = None
    try:
        assert ready.wait(2)
        old = submit(first)
        client.send_command(old)
        assert done.wait(2)
        server.close()
        first.close()
        ready.clear()
        done.clear()
        second = TextInteractionController(runtime)
        server = LocalStateServer(AgentStateBus(), endpoint_path=path, text_controller=second)
        server.start()
        assert ready.wait(2)
        assert bus.snapshot['generation'] == second.generation
        assert len(runtime.calls) == 1
        client.send_command(old)
        assert done.wait(2)
        assert events[-1]['status'] == 'error' and len(runtime.calls) == 1
        done.clear()
        client.send_command(submit(second))
        assert done.wait(2)
        assert len(runtime.calls) == 2
    finally:
        client.close()
        server.close()
        if second:
            second.close()
        elif first._thread.is_alive():
            first.close()
