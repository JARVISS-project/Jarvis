from dataclasses import replace
import json
from threading import Event, enumerate as threads
from uuid import uuid4

import pytest

from jarvis.application.application_dispatcher import ApplicationNormalizer
from jarvis.application.text_interaction import TextInteractionController
from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.memory.conversation import ConversationMemory
from jarvis.tools.registry import ToolRegistry
from tests.integration.test_administration_23bh import NoLLM
from tests.desktop.test_application_catalog_23bi import system, allow, run
from jarvis.desktop.application_windows import ApplicationWindow
from jarvis.security.permissions import scoped_resource


def build(tmp_path):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    registry = ToolRegistry()
    for tool in tools.values():
        registry.register(tool)
    config = JarvisConfig(settings_path=tmp_path / 'settings.json', filesystem_base_directory=tmp_path,
        obsidian_enabled=False, skills_obsidian_mirror_enabled=False, local_control_enabled=True)
    harness = Harness(NoLLM(), registry, executor, ConversationMemory(), config=config, application_control=service)
    return harness, entry, service, backend, permissions, tools


@pytest.mark.parametrize('text', ['Não abra PRIVATE APP.', 'Abra PRIVATE APP e feche outro.', 'Abra PRIVATE APP amanhã.', 'Abra desconhecido.', '/app open C:\\evil.exe', '/app open app-' + 'a' * 32 + ' extra'])
def test_negation_ambiguity_residual_extra_no_effect(tmp_path, text):
    h, entry, service, backend, permissions, tools = build(tmp_path)
    try:
        allow(service, entry, 'open')
        decision = h.application_dispatcher.dispatch(text)
        assert decision.handled and not decision.executed and not backend.effects
    finally:
        h.close_confirmation_sessions()


@pytest.mark.parametrize('response_mode', ['text', 'voice'])
def test_existing_harness_channels_same_block_and_allow(tmp_path, response_mode):
    h, entry, service, backend, permissions, tools = build(tmp_path)
    try:
        assert 'bloquead' in h.processar_mensagem('/app open ' + entry.id, response_mode=response_mode).lower()
        assert not backend.effects
        allow(service, entry, 'open')
        assert 'verificada' in h.processar_mensagem('Abra PRIVATE APP.', response_mode=response_mode)
        allow(service, entry, 'window')
        assert 'verificada' in h.processar_mensagem('Maximize PRIVATE APP.', response_mode=response_mode)
        allow(service, entry, 'window', 'block')
        h.processar_mensagem('Minimize PRIVATE APP.', response_mode=response_mode)
        assert [effect[0] for effect in backend.effects] == ['open', 'maximize']
    finally:
        h.close_confirmation_sessions()


def test_dispatcher_replay_fenced(tmp_path):
    h, entry, service, backend, permissions, tools = build(tmp_path)
    try:
        allow(service, entry, 'open')
        interaction = h.application_dispatcher.new_interaction_id()
        first = h.application_dispatcher.dispatch('/app open ' + entry.id, interaction_id=interaction)
        second = h.application_dispatcher.dispatch('/app open ' + entry.id, interaction_id=interaction)
        assert first == second and len(backend.effects) == 1
    finally:
        h.close_confirmation_sessions()


@pytest.mark.parametrize('outcome', ['confirm', 'cancel', 'revoke', 'replace', 'wrong_confirmation', 'expire'])
def test_close_confirmation_bound_single_use(tmp_path, outcome):
    h, entry, service, backend, permissions, tools = build(tmp_path)
    try:
        allow(service, entry, 'close')
        backend.instances = [ApplicationWindow(1, 2, 3, 4, 'class', 'PRIVATE TITLE')]
        response = h.processar_mensagem('/app close ' + entry.id)
        context = h.current_confirmation_context()
        assert context and 'PRIVATE APP' in response and not backend.effects
        if outcome == 'cancel':
            h.processar_mensagem('não', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
        else:
            if outcome == 'revoke':
                h.administration.dispatch('apps.permission', {'application': entry.id, 'fingerprint': entry.fingerprint, 'group': 'close', 'mode': 'block', 'revision': permissions.read()[1], 'remove': False})
                assert h.current_confirmation_context() is None
            elif outcome == 'replace':
                backend.instances = [replace(backend.instances[0], birth=99)]
            elif outcome == 'expire':
                expires_at = h.confirmation_sessions.current().expires_at
                h.confirmation_sessions.clock = lambda: expires_at + 1
                assert h.current_confirmation_context() is None
            supplied = (context[0], uuid4().hex) if outcome == 'wrong_confirmation' else context
            h.processar_mensagem('sim', confirmation_session_id=supplied[0], confirmation_challenge_id=supplied[1])
        assert len(backend.effects) == (1 if outcome == 'confirm' else 0)
        if outcome == 'confirm':
            h.processar_mensagem('sim', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
            assert len(backend.effects) == 1
    finally:
        h.close_confirmation_sessions()


def test_ui_text_worker_real_harness_restart_cancel_and_no_residual(tmp_path):
    h, entry, service, backend, permissions, tools = build(tmp_path)
    controller = TextInteractionController(h)
    events, done = [], Event()
    def reply(event):
        events.append(event)
        if event.get('status') in {'done', 'error', 'confirmation', 'cancelled'}:
            done.set()
    try:
        allow(service, entry, 'open')
        payload = {'command': 'text_submit', 'generation': controller.generation, 'interaction': uuid4().hex, 'text': '/app open ' + entry.id}
        controller.handle('owner', payload, reply)
        assert done.wait(2) and events[-1]['status'] == 'done' and len(backend.effects) == 1
        controller.handle('owner', payload, reply)
        assert len(backend.effects) == 1
        allow(service, entry, 'close')
        done.clear()
        payload = {**payload, 'interaction': uuid4().hex, 'text': '/app close ' + entry.id}
        controller.handle('owner', payload, reply)
        assert done.wait(2) and events[-1]['status'] == 'confirmation'
        done.clear()
        controller.handle('owner', {'command': 'text_cancel', 'generation': controller.generation, 'interaction': payload['interaction']}, reply)
        assert done.wait(2) and not any(effect[0] == 'close' for effect in backend.effects)
        controller.close()
        restarted = TextInteractionController(h)
        try:
            restarted.handle('owner', payload, reply)
            assert events[-1]['status'] == 'error' and len(backend.effects) == 1
        finally:
            restarted.close()
    finally:
        controller.close()
        h.close_confirmation_sessions()
    assert not any(thread.name == 'jarvis-ui-text' for thread in threads())


def test_path_restrictions_disables_indirect_application_route(tmp_path):
    h, entry, service, backend, permissions, tools = build(tmp_path)
    try:
        allow(service, entry, 'open')
        permissions.set_rule('path:' + str(tmp_path / 'protected'), 'read', 'block', expected_revision=permissions.read()[1])
        assert service.mode(entry, 'open').mode == 'block'
        h.processar_mensagem('/app open ' + entry.id)
        assert not backend.effects
    finally:
        h.close_confirmation_sessions()


def test_legacy_desktop_route_disabled_and_history_cannot_evict_replay(tmp_path):
    from types import SimpleNamespace
    h, entry, service, backend, permissions, tools = build(tmp_path)
    try:
        for name in ['desktop_open_application', 'desktop_focus_window', 'desktop_close_window', 'desktop_move_pointer_to_window']:
            with pytest.raises(PermissionError):
                permissions.check_tool(SimpleNamespace(name=name), {})
        allow(service, entry, 'open')
        dispatcher = h.application_dispatcher
        dispatcher._max_history = 1
        correlation = dispatcher.new_interaction_id()
        first = dispatcher.dispatch('/app open ' + entry.id, interaction_id=correlation)
        assert first.executed
        assert dispatcher.dispatch('/app open ' + entry.id).reason == 'interaction_limit'
        assert dispatcher.dispatch('/app open ' + entry.id, interaction_id=correlation) == first
        assert len(backend.effects) == 1
    finally:
        h.close_confirmation_sessions()
