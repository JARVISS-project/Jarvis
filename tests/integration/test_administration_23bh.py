from datetime import datetime, timezone
from threading import Event, enumerate as threads
from uuid import uuid4

import pytest

from jarvis.application.administration import AdministrationService, AdministrationController, MemoryDraft, PermissionChange
from jarvis.application.state import AgentStateBus
from jarvis.application.state_ipc import LocalStateServer, LocalStateClient, StateEndpoint
from jarvis.application.text_interaction import TextEventBus, TextInteractionController
from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.managed import ManagedMemoryStore
from jarvis.memory.sqlite_store import SQLiteMemoryStore
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.permissions import PermissionStore
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.registry import ToolRegistry


class NoLLM(LLMClient):
    def generate(self, messages, tools=None):
        raise AssertionError('Administrative/local requests must not call a provider')


class Confirm(ConfirmationProvider):
    def confirm(self, action, target, risk):
        return True


def build(tmp_path):
    config = JarvisConfig(settings_path=tmp_path / 'settings.json', database_path=tmp_path / 'memory.db',
        filesystem_base_directory=tmp_path, obsidian_enabled=False, skills_obsidian_mirror_enabled=False)
    permissions = PermissionStore(tmp_path / 'permissions.json', (tmp_path,))
    store = ManagedMemoryStore(SQLiteMemoryStore(config.database_path), permissions)
    registry = ToolRegistry()
    guard = FileSystemGuard(tmp_path, permissions)
    registry.register(ReadFileTool(guard))
    registry.register(CreateFileTool(guard))
    executor = ToolExecutor(SafetyManager(Confirm()), AuditLogger(tmp_path / 'audit.log'),
        CapabilityPolicy({Capability.FILESYSTEM_READ: True, Capability.FILESYSTEM_WRITE: True}), permissions=permissions)
    harness = Harness(NoLLM(), registry, executor, ConversationMemory(), memory_store=store, config=config)
    return harness, harness.administration


def draft(content='prefiro café', **kwargs):
    return MemoryDraft(content=content, category=kwargs.pop('category', 'preferência'),
        context=kwargs.pop('context', True), external=kwargs.pop('external', False), retention_days=kwargs.pop('retention_days', None), **kwargs)


def change(service, resource, mode, action='use'):
    return service.set_permission(PermissionChange(resource, action, mode, service.inventory()['revision']))


def test_memory_sqlite_add_search_edit_restart_forget_and_derived_context(tmp_path):
    h, service = build(tmp_path)
    try:
        assert service.list_memories()['rows'] == []
        service.save_memory(draft())
        row = service.list_memories('café')['rows'][0]
        assert row['origin'] == 'user_explicit' and row['created'] and not row['external']
        h.memory.add_user_message('contexto derivado antigo')
        service.save_memory(draft('prefiro chá', category='fato', id=row['id'], version=row['version'], retention_days=30))
        assert h.memory.count() == 0
        assert not service.list_memories('café')['rows']
        restarted = SQLiteMemoryStore(h.config.database_path)
        assert restarted.get_all()[0].content == 'prefiro chá'
        assert restarted.get_all()[0].retention_days == 30
        assert h.memory_store.get_context('chá')
        prepared = service.prepare_forget(row['id'])
        service.forget(prepared['token'])
        assert not restarted.get_all() and not h.memory_store.get_context('chá')
        assert not service.list_memories()['rows']
        with pytest.raises(PermissionError):
            service.forget(prepared['token'])
    finally:
        h.close_confirmation_sessions()


@pytest.mark.parametrize('category', ['preferência', 'fato', 'tarefa'])
def test_categories_and_metadata_retention_preserved(tmp_path, category):
    h, service = build(tmp_path)
    try:
        service.save_memory(draft(category=category, retention_days=1))
        assert service.list_memories()['rows'][0]['category'] == category
    finally:
        h.close_confirmation_sessions()


def test_temporary_session_blocks_ui_cli_and_context_controls(tmp_path):
    h, service = build(tmp_path)
    try:
        service.save_memory(draft())
        service.dispatch('memory.session', {'temporary': True})
        with pytest.raises(PermissionError):
            service.save_memory(draft('não guardar'))
        assert 'bloqueada' in h.processar_mensagem('/remember não guardar')
        assert len(service.list_memories()['rows']) == 1
        change(service, 'memory.context', 'block')
        assert h.memory_store.get_relevant('café') == []
        assert service.list_memories()['rows']  # Administration remains visible.
        service.dispatch('memory.session', {'temporary': False})
        change(service, 'memory.save', 'block')
        assert 'bloqueada' in h.processar_mensagem('/remember não guardar')
    finally:
        h.close_confirmation_sessions()


def test_legacy_consent_is_not_external_consent_and_individual_gate(tmp_path):
    h, service = build(tmp_path)
    try:
        h.memory_store.store.add('café legado', 'fato', consent_given=True)
        assert not h.memory_store.get_context('café', external=True)
        with pytest.raises(PermissionError):
            service.save_memory(draft(external=True))
        change(service, 'memory.external', 'allow')
        assert not h.memory_store.get_context('café', external=True)
        service.save_memory(draft(external=True))
        assert len(h.memory_store.get_context('café', external=True)) == 1
        change(service, 'memory.external', 'block')
        assert not h.memory_store.get_context('café', external=True)
    finally:
        h.close_confirmation_sessions()


@pytest.mark.parametrize('content', ['api_key=sk-private1234567890', 'senha: segredo', 'Bearer secrettoken', '-----BEGIN PRIVATE KEY-----'])  # gitleaks:allow - synthetic credential-filter test vector
def test_credentials_never_enter_managed_memory(tmp_path, content):
    h, service = build(tmp_path)
    try:
        with pytest.raises(ValueError, match='Credenciais'):
            service.save_memory(draft(content))
        assert service.list_memories()['rows'] == []
        assert not (tmp_path / 'audit.log').exists()
    finally:
        h.close_confirmation_sessions()


def test_forget_expiration_cancel_and_concurrent_change(tmp_path):
    h, service = build(tmp_path)
    try:
        service.save_memory(draft())
        row = service.list_memories()['rows'][0]
        prepared = service.prepare_forget(row['id'])
        assert service.list_memories()['rows']  # Cancellation/no confirmation has no effect.
        service._forget[prepared['token']] = (row['id'], row['version'], datetime(2000, 1, 1, tzinfo=timezone.utc), None, service.permissions.read()[1])
        with pytest.raises(PermissionError):
            service.forget(prepared['token'])
        prepared = service.prepare_forget(row['id'])
        service.save_memory(draft('texto alterado', id=row['id'], version=row['version']))
        with pytest.raises(PermissionError):
            service.forget(prepared['token'])
        with pytest.raises(PermissionError):
            service.save_memory(draft('edição antiga', id=row['id'], version=row['version']))
        assert service.list_memories()['rows'][0]['content'] == 'texto alterado'
    finally:
        h.close_confirmation_sessions()


@pytest.mark.parametrize('method,arguments', [('permissions.list', {'grant_all': True}),
    ('permissions.set', {'resource': '*'}), ('memory.session', {'temporary': 1}),
    ('memory.save', {'content': 'extra'}), ('memory.delete_all', {})])
def test_administrative_extra_arguments_and_unknown_actions_rejected(tmp_path, method, arguments):
    h, service = build(tmp_path)
    try:
        with pytest.raises((ValueError, PermissionError)):
            service.dispatch(method, arguments)
        assert not service.list_memories()['rows'] and not service.permissions.path.exists()
    finally:
        h.close_confirmation_sessions()


def test_inventory_no_future_controls_or_authority_increase(tmp_path):
    h, service = build(tmp_path)
    try:
        rows = service.inventory()['rows']
        # 23BR exposes explicit restrictions; defaults remain blocked and a
        # permission choice alone cannot create a configured API connection.
        external = [r for r in rows if r['resource'] in {'model.external', 'delegation.execute'}]
        assert all(r['mode'] == 'block' and r['modes'] == ['block', 'ask', 'allow'] for r in external)
        assert not any('google_calendar' in r['resource'] for r in rows)
        for resource in ('tool:shell', 'tool:google_calendar_create_event'):
            with pytest.raises(PermissionError):
                change(service, resource, 'allow')
        assert not service.permissions.path.exists()
        change(service, 'model.external', 'allow')
        assert not h.delegation.path.exists()
        with pytest.raises(PermissionError):
            h.delegation.prepare('openai', 'Neutral question')
    finally:
        h.close_confirmation_sessions()


def test_inventory_effective_policy_includes_ancestor_capture_and_temporary_blocks(tmp_path):
    from jarvis.tools.base import Tool
    from jarvis.security.risk import RiskLevel
    class VisionBoundary(Tool):
        name, description, risk = 'vision_extract_text', 'Visão configurada', RiskLevel.READ_ONLY
        parameters = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
        def get_target(self, **kwargs): return 'screen'
        def execute(self, **kwargs): raise AssertionError('No capture')
    h, service = build(tmp_path)
    try:
        h.tool_registry.register(VisionBoundary())
        change(service, 'path:' + str(tmp_path), 'block', 'read')
        change(service, 'path:' + str(tmp_path / 'descendant'), 'allow', 'read')
        service.permissions.set_rule('capture.screen', 'use', 'block', expected_revision=service.permissions.read()[1])
        h.memory_store.set_temporary(True)
        rows = {r['resource']: r for r in service.inventory()['rows']}
        assert rows['path:' + str(tmp_path / 'descendant')]['effective'] == 'block'
        assert rows['tool:vision_extract_text']['effective'] == 'bloqueado: captura de tela'
        assert rows['memory.save']['effective'] == 'bloqueado: sessão temporária'
        assert rows['memory.edit']['effective'] == 'bloqueado: sessão temporária'
    finally:
        h.close_confirmation_sessions()


def test_harness_pending_ask_read_invalidated_by_revocation(tmp_path):
    h, service = build(tmp_path)
    try:
        (tmp_path / 'file.txt').write_text('private result')
        change(service, 'tool:read_file', 'ask')
        result = h.tool_executor.execute_with_result(h.tool_registry.get('read_file'), path='file.txt', _interaction_id='test')
        assert result.status == 'confirmation_required'
        context = h.current_confirmation_context()
        assert context is not None
        change(service, 'tool:read_file', 'block')
        assert h.current_confirmation_context() is None
        response = h.processar_mensagem('sim', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
        assert 'private result' not in response
        assert h.tool_executor.execute_with_result(h.tool_registry.get('read_file'), path='file.txt').status == 'security_blocked'
    finally:
        h.close_confirmation_sessions()


def test_ask_read_harness_confirmation_executes_once(tmp_path):
    h, service = build(tmp_path)
    try:
        (tmp_path / 'file.txt').write_text('private result')
        change(service, 'tool:read_file', 'ask')
        h.tool_executor.execute_with_result(h.tool_registry.get('read_file'), path='file.txt', _interaction_id='test')
        context = h.current_confirmation_context()
        assert h.processar_mensagem('sim', confirmation_session_id=context[0], confirmation_challenge_id=context[1]) == 'private result'
        assert h.current_confirmation_context() is None
        assert 'private result' not in (tmp_path / 'audit.log').read_text()
    finally:
        h.close_confirmation_sessions()


def test_real_loopback_owner_reply_restart_and_cleanup(tmp_path):
    h, service = build(tmp_path)
    text = TextInteractionController(h)
    admin = AdministrationController(service, text.generation)
    bus = TextEventBus()
    got = Event()
    received = []
    def receive(event):
        received.append(event)
        if event.get('type') == 'admin':
            got.set()
    unsubscribe = bus.subscribe(receive)
    server = LocalStateServer(AgentStateBus(), endpoint_path=tmp_path / 'endpoint.json', text_controller=text, admin_controller=admin)
    endpoint = server.start()
    assert admin.authorization not in (tmp_path / 'endpoint.json').read_text()
    assert endpoint.administrative_authorization() == admin.authorization
    assert 'admin_secret' not in repr(endpoint)
    assert StateEndpoint.read(tmp_path / 'endpoint.json').administrative_authorization() == admin.authorization
    client = LocalStateClient(AgentStateBus(), endpoint, text_bus=bus)
    client.start()
    try:
        # Existing protocol has a ready event; wait via bounded Event callback.
        ready = Event()
        unsub_ready = bus.subscribe(lambda e: ready.set() if e.get('status') == 'ready' else None)
        assert ready.wait(2)
        payload = {'command': 'admin_request', 'generation': text.generation, 'interaction': uuid4().hex,
                   'method': 'memory.list', 'arguments': {'query': ''}}
        client.send_command(payload)
        assert got.wait(2)
        assert received[-1]['data']['rows'] == []
        got.clear()
        client.send_command(payload)
        assert got.wait(2) and received[-1]['status'] == 'error'
        assert LocalStateServer._decode_text_command(b'{"command":"admin_request","generation":"a","interaction":"b","method":"memory.list","arguments":{"query":"","query":"x"}}') is None
        unsub_ready()
    finally:
        unsubscribe()
        client.close()
        server.close()
        admin.close()
        text.close()
        h.close_confirmation_sessions()
    assert not any(t.name in {'jarvis-ui-admin', 'jarvis-ui-text'} for t in threads())


@pytest.mark.parametrize('authorization', [None, '0' * 64, 'é' * 64])
def test_admin_authentication_fails_before_any_store_access(tmp_path, authorization):
    h, service = build(tmp_path)
    controller = AdministrationController(service, uuid4().hex)
    replies = []
    payload = {'command': 'admin_request', 'generation': controller.generation, 'interaction': uuid4().hex,
               'method': 'memory.list', 'arguments': {'query': ''}}
    if authorization is not None:
        payload['authorization'] = authorization
    try:
        controller.handle(payload, replies.append)
        assert replies[0]['status'] == 'error' and 'data' not in replies[0]
        assert not service.permissions.path.exists()
    finally:
        controller.close()
        h.close_confirmation_sessions()


def test_forget_owner_binding_and_permission_revision(tmp_path):
    h, service = build(tmp_path)
    first, second = object(), object()
    try:
        service.save_memory(draft())
        row = service.list_memories()['rows'][0]
        prepared = service.prepare_forget(row['id'], actor=first)
        with pytest.raises(PermissionError):
            service.forget(prepared['token'], actor=second)
        assert service.list_memories()['rows']
        prepared = service.prepare_forget(row['id'], actor=first)
        change(service, 'memory.delete', 'block')
        with pytest.raises(PermissionError):
            service.forget(prepared['token'], actor=first)
        assert service.list_memories()['rows']
        assert 'bloqueada' in h.processar_mensagem('/forget ' + row['id'])
    finally:
        h.close_confirmation_sessions()


def test_cli_forget_uses_private_tool_and_bound_confirmation(tmp_path):
    h, service = build(tmp_path)
    try:
        service.save_memory(draft())
        row = service.list_memories()['rows'][0]
        assert h.tool_registry.get('memory_forget') is None
        response = h.processar_mensagem('/forget ' + row['id'])
        assert service.list_memories()['rows'] and h.current_confirmation_context()
        context = h.current_confirmation_context()
        assert 'esquecida' in h.processar_mensagem('sim', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
        assert not service.list_memories()['rows'] and h.current_confirmation_context() is None
        with pytest.raises(PermissionError):
            h._memory_forget_tool.execute(id=row['id'])
        assert 'prefiro café' not in (tmp_path / 'audit.log').read_text()
    finally:
        h.close_confirmation_sessions()


def test_local_skill_revalidates_each_step_and_cannot_override_deny(tmp_path):
    from jarvis.skills.executor import SkillExecutor
    from jarvis.skills.models import SkillDefinition, SkillStep
    h, service = build(tmp_path)
    try:
        change(service, 'tool:create_file', 'block')
        skill = SkillDefinition('memory says allow', (SkillStep('create_file', {'path': 'no.txt', 'content': 'private'}),))
        result = SkillExecutor(h.tool_registry, h.tool_executor).execute(skill)
        assert not result.success and not (tmp_path / 'no.txt').exists()
        assert result.results[0].status == 'security_blocked'
    finally:
        h.close_confirmation_sessions()


@pytest.mark.parametrize('channel', ['cli', 'voice', 'ui_text'])
def test_tool_block_shared_by_real_harness_channels_without_device(tmp_path, monkeypatch, channel):
    from jarvis.llm.types import LLMResponse, ToolCallRequest
    h, service = build(tmp_path)
    text = None
    executed = []
    generated = []
    def generate(messages, tools=None):
        generated.append(messages)
        if len(generated) == 1:
            return LLMResponse('', [ToolCallRequest('read_file', {'path': 'file.txt'})])
        return LLMResponse('Operação bloqueada.')
    monkeypatch.setattr(h.llm, 'generate', generate)
    monkeypatch.setattr(h.tool_registry.get('read_file'), 'execute', lambda **kwargs: executed.append(kwargs) or 'private sentinel')
    (tmp_path / 'file.txt').write_text('private sentinel')
    change(service, 'tool:read_file', 'block')
    try:
        prompt = 'Leia o arquivo file.txt no projeto.'
        if channel == 'ui_text':
            text = TextInteractionController(h)
            done, events = Event(), []
            def receive(event):
                events.append(event)
                if event['status'] in {'done', 'error'}:
                    done.set()
            text.handle(object(), {'command': 'text_submit', 'generation': text.generation,
                'interaction': uuid4().hex, 'text': prompt}, receive)
            assert done.wait(2)
            assert 'private sentinel' not in str(events)
        else:
            response = h.processar_mensagem(prompt, response_mode='voice' if channel == 'voice' else 'text')
            assert 'private sentinel' not in response
        assert executed == []
        assert 'read_file' in (tmp_path / 'audit.log').read_text()
        assert 'security_blocked' in (tmp_path / 'audit.log').read_text()
    finally:
        if text is not None:
            text.close()
        h.close_confirmation_sessions()


def test_actual_recorder_boundary_stops_after_permission_revocation_without_mic(tmp_path):
    from jarvis.audio.input import PvRecorderAudioInput
    class Recorder:
        starts = reads = stops = deletes = 0
        def __init__(self, **kwargs): pass
        def start(self): self.starts += 1
        def read(self): self.reads += 1; return [0]
        def stop(self): self.stops += 1
        def delete(self): self.deletes += 1
    p = PermissionStore(tmp_path / 'permissions.json', (tmp_path,))
    source = PvRecorderAudioInput(1, recorder_factory=Recorder, permission_check=lambda: p.require('microphone.capture'))
    try:
        source.start()
        assert source.read_frame() == [0]
        p.set_rule('microphone.capture', 'use', 'block', expected_revision=p.read()[1])
        with pytest.raises(PermissionError):
            source.read_frame()
        assert source._recorder.reads == 1 and source._recorder.stops == 1
    finally:
        source.close()
    assert source._recorder.deletes == 1


def test_settings_probe_never_constructs_device_when_blocked_and_cleans_on_revocation(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from jarvis.audio.devices import probe_microphone
    p = PermissionStore(tmp_path / 'permissions.json', (tmp_path,))
    calls = []
    class Recorder:
        def __init__(self, **kwargs): calls.append('construct')
        def start(self): calls.append('start')
        def read(self):
            calls.append('read')
            p.set_rule('microphone.capture', 'use', 'block', expected_revision=p.read()[1])
            return [0]
        def stop(self): calls.append('stop')
        def delete(self): calls.append('delete')
    monkeypatch.setitem(sys.modules, 'pvrecorder', SimpleNamespace(PvRecorder=Recorder))
    p.set_rule('microphone.capture', 'use', 'block', expected_revision=p.read()[1])
    assert not probe_microphone(-1, 1, 2, permission_check=lambda: p.require('microphone.capture'))
    assert calls == []
    p.set_rule('microphone.capture', 'use', 'allow', expected_revision=p.read()[1])
    assert not probe_microphone(-1, 1, 2, permission_check=lambda: p.require('microphone.capture'))
    assert calls == ['construct', 'start', 'read', 'stop', 'delete']


def test_scoped_application_controls_preserve_other_ids_and_global_deny(tmp_path):
    from jarvis.local_control.service import LocalControlService
    from jarvis.tools.local_control import LocalApplicationOpenTool
    from jarvis.security.permissions import scoped_resource
    from tests.local_control.test_local_control import FakeBackend
    h, service = build(tmp_path)
    backend = FakeBackend()
    tool = LocalApplicationOpenTool(LocalControlService(backend))
    h.tool_registry.register(tool)
    h.tool_executor.capability_policy = CapabilityPolicy({Capability.LOCAL_CONTROL: True})
    try:
        spotify = scoped_resource(tool.name, 'application', 'spotify')
        assert any(r['resource'] == spotify for r in service.inventory()['rows'])
        change(service, spotify, 'block')
        assert h.tool_executor.execute_with_result(tool, application='spotify').status == 'security_blocked'
        assert backend.calls == []
        assert h.tool_executor.execute_with_result(tool, application='obsidian').status == 'success'
        assert backend.calls == [('open', 'obsidian')]
        change(service, 'tool:local_application_open', 'block')
        change(service, spotify, 'allow')
        assert h.tool_executor.execute_with_result(tool, application='spotify').status == 'security_blocked'
        assert backend.calls == [('open', 'obsidian')]
    finally:
        h.close_confirmation_sessions()


def test_remote_chat_readonly_help_and_local_permissions_shared(tmp_path):
    from jarvis.remote.core_port import HarnessRemoteCorePort
    h, service = build(tmp_path)
    try:
        remote = HarnessRemoteCorePort(h)
        change(service, 'tool:read_file', 'block')
        answer = remote.process_chat('O que o JARVIS pode acessar?')
        assert 'bloqueado' in answer and 'Credencial não concede autorização' in answer
        assert 'bloqueado' in h.processar_mensagem('/permissions')
        assert h.memory.count() == 0
    finally:
        h.close_confirmation_sessions()


def test_legacy_categories_preserved_and_separate_edit_block(tmp_path):
    h, service = build(tmp_path)
    try:
        service.save_memory(draft(category='lembrete', retention_days=30))
        row = service.list_memories()['rows'][0]
        change(service, 'memory.edit', 'block')
        with pytest.raises(PermissionError):
            service.save_memory(draft('edição', category='lembrete', id=row['id'], version=row['version']))
        assert service.list_memories()['rows'][0]['category'] == 'lembrete'
        service.save_memory(draft('nova memória'))
        assert len(service.list_memories()['rows']) == 2
    finally:
        h.close_confirmation_sessions()


def test_metadata_credentials_rejected_and_temporary_resets_only_on_new_runtime(tmp_path):
    h, service = build(tmp_path)
    restarted = None
    try:
        with pytest.raises(ValueError):
            h.memory_store.add('fato', 'fato', metadata={'access_token': 'private-secret'})
        service.dispatch('memory.session', {'temporary': True})
        restarted, other_service = build(tmp_path)
        assert not other_service.memory.temporary
        assert not service.list_memories()['rows']
    finally:
        h.close_confirmation_sessions()
        if restarted:
            restarted.close_confirmation_sessions()


def test_forgetting_during_provider_call_aborts_derived_response_and_next_context(tmp_path):
    from jarvis.llm.types import LLMResponse
    h, service = build(tmp_path)
    class Boundary:
        calls = 0
        def generate(self, **kwargs):
            self.calls += 1
            prepared = service.prepare_forget(service.list_memories()['rows'][0]['id'])
            service.forget(prepared['token'])
            return LLMResponse('previously observed private-memory')
    try:
        service.save_memory(draft('private-memory'))
        h.memory.add_user_message('previously derived context')
        messages = h.response_coordinator.system_context(user_message='private-memory')
        h.llm = boundary = Boundary()
        with pytest.raises(PermissionError, match='Contexto'):
            h._generate_llm_response(messages, [])
        assert boundary.calls == 1 and h.memory.count() == 0
        assert not h.memory_store.get_context('private-memory')
        with pytest.raises(PermissionError):
            h._generate_llm_response(messages, [])
        assert boundary.calls == 1
    finally:
        h.close_confirmation_sessions()
