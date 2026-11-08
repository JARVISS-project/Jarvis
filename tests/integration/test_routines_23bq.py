from copy import deepcopy
import json
from threading import Event, Thread

import pytest

from tests.integration.test_daily_briefing_23bf import make
from jarvis.application.routines import RoutineService, digest
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


class BoundaryTool(Tool):
    name = 'get_datetime'
    description = 'Fake effect boundary'
    risk = RiskLevel.READ_ONLY
    parameters = {'type': 'object', 'properties': {}, 'additionalProperties': False}

    def __init__(self):
        self.calls = 0
        self.callback = lambda: None

    def get_target(self, **kwargs):
        return 'neutral-target'

    def execute(self, **kwargs):
        self.calls += 1
        self.callback()
        return 'neutral receipt'


def configured(tmp_path, steps=2):
    h, boundary, memory = make(tmp_path)
    tool = BoundaryTool()
    h.tool_registry._tools['get_datetime'] = tool
    service = h.routines
    row = deepcopy(service.definitions()[1])
    row.update(enabled=True, steps=[{'kind': 'datetime', 'resource': '', 'identity': ''} for _ in range(steps)])
    service.save(row, digest(service.definitions()))
    return h, tool


def test_templates_disabled_editing_and_preview_have_no_effect(tmp_path):
    h, tool = configured(tmp_path)
    h.routines.preview('study')
    assert tool.calls == 0
    assert all(not r['enabled'] for r in h.routines.definitions() if r['id'] != 'study')


def test_question_about_routine_only_previews_and_never_executes(tmp_path):
    h,tool=configured(tmp_path)
    h.processar_mensagem('Modo estudo?',manage_runtime=False)
    assert tool.calls==0


def test_order_receipts_once_replay_and_private_journal(tmp_path):
    h, tool = configured(tmp_path)
    preview = h.routines.preview('study')
    assert 'concluída' in h.routines.start(preview['token'])
    assert tool.calls == 2
    assert 'antiga' in h.routines.start(preview['token']) and tool.calls == 2
    run = json.loads(h.routines.history_path.read_text())[0]
    assert [s['status'] for s in run['steps']] == ['success', 'success']
    assert len({s['id'] for s in run['steps']}) == 2
    assert 'neutral receipt' not in h.routines.history_path.read_text()
    assert h.memory.get_history() == []


@pytest.mark.parametrize('change', ['disabled', 'capability', 'tamper'])
def test_unavailable_disabled_or_tampered_plan_never_executes(tmp_path, change):
    h, tool = configured(tmp_path)
    p = h.routines.preview('study')
    if change == 'tamper':
        rows = h.routines.definitions(); next(r for r in rows if r['id']=='study')['steps'] = []
        h.routines.path.write_text(json.dumps(rows))
    elif change == 'disabled':
        rows = h.routines.definitions(); revision=digest(rows);row = next(r for r in rows if r['id'] == 'study')
        row['enabled'] = False;h.routines.save(row,revision)
    else:
        h.tool_executor.capability_policy.require_tool = lambda name: (_ for _ in ()).throw(PermissionError())
    response = h.routines.start(p['token'])
    assert 'concluída' not in response and tool.calls == 0


def test_cancellation_stops_next_effect_and_concurrency(tmp_path):
    h, tool = configured(tmp_path)
    entered, release = Event(), Event()
    tool.callback = lambda: (entered.set(), release.wait(5))
    token = h.routines.preview('study')['token']
    other = h.routines.preview('study')['token']
    worker = Thread(target=lambda: h.routines.start(token))
    worker.start();assert entered.wait(3)
    assert 'ocupado' in h.routines.start(other)
    h.cancel_active_work();release.set();worker.join(5)
    assert not worker.is_alive() and tool.calls == 1
    assert h.routines.history[-1]['status'] == 'cancelled'


def test_uncertain_exception_no_retry_restart_does_not_resume(tmp_path):
    h, tool = configured(tmp_path)
    tool.callback = lambda: (_ for _ in ()).throw(RuntimeError('uncertain effect'))
    token = h.routines.preview('study')['token']
    h.routines.start(token)
    assert tool.calls == 1 and h.routines.history[-1]['status'] == 'interrupted'
    h.routines.history[-1]['status'] = 'executing';h.routines.record()
    restarted = RoutineService(h)
    assert restarted.history[-1]['status'] == 'interrupted'
    assert 'antiga' in restarted.start(token) and tool.calls == 1


def test_each_confirmation_bound_then_next_and_stale_rejected(tmp_path):
    h, tool = configured(tmp_path)
    tool.risk = RiskLevel.DESTRUCTIVE
    response = h.processar_mensagem('Modo estudo.', manage_runtime=False)
    context = h.current_confirmation_context();assert context and tool.calls == 0
    h.processar_mensagem('sim',manage_runtime=False,confirmation_session_id=context[0],confirmation_challenge_id=context[1])
    second = h.current_confirmation_context();assert second and second != context and tool.calls == 1
    h.processar_mensagem('sim',manage_runtime=False,confirmation_session_id=context[0],confirmation_challenge_id=context[1])
    assert tool.calls == 1
    h.processar_mensagem('sim',manage_runtime=False,confirmation_session_id=second[0],confirmation_challenge_id=second[1])
    assert tool.calls == 2 and h.routines.active is None


def test_edit_pending_invalidates_confirmation(tmp_path):
    h, tool = configured(tmp_path);tool.risk = RiskLevel.DESTRUCTIVE
    h.processar_mensagem('modo estudo',manage_runtime=False);context=h.current_confirmation_context()
    rows=h.routines.definitions();revision=digest(rows);row=next(r for r in rows if r['id']=='study');row['steps'].reverse();row['name']='Novo nome'
    h.routines.save(row,revision)
    h.processar_mensagem('sim',manage_runtime=False,confirmation_session_id=context[0],confirmation_challenge_id=context[1])
    assert tool.calls == 0 and h.current_confirmation_context() is None


def test_command_collisions_and_out_of_scope_rejected(tmp_path):
    h, tool = configured(tmp_path)
    rows=h.routines.definitions();row=deepcopy(rows[0]);row['command']='abrir navegador'
    with pytest.raises(ValueError):h.routines.save(row,digest(rows))
    row['command']='rotina permitido';row['steps']=[{'kind':'shell','resource':'whoami','identity':''}]
    with pytest.raises(ValueError):h.routines.save(row,digest(rows))
    row=deepcopy(rows[0]);row['name']='Modo estudo'
    with pytest.raises(ValueError):h.routines.save(row,digest(rows))


def test_day_sources_use_executor_today_and_never_persist_contents(tmp_path):
    h,boundary,memory=make(tmp_path)
    memory.add('Conteudo privado de tarefa','tarefa')
    rows=h.routines.definitions();revision=digest(rows);row=rows[0];row.update(enabled=True,steps=[
        {'kind':'calendar','resource':'principal','identity':''},
        {'kind':'briefing','resource':'local','identity':''}])
    h.routines.save(row,revision)
    text=h.processar_mensagem('começar meu dia',manage_runtime=False)
    assert 'Conteudo privado' in text and len(boundary.calls)==1
    assert h.memory.get_history()==[]
    assert 'Conteudo privado' not in h.routines.history_path.read_text()


@pytest.mark.parametrize('mode', ['block', 'ask'])
def test_permission_denial_and_revocation_bound_confirmation(tmp_path, mode):
    from jarvis.security.permissions import PermissionStore
    h,tool=configured(tmp_path)
    tool.name='local_briefing_memory'
    h._briefing_memory_tool=tool
    rows=h.routines.definitions();revision=digest(rows);row=next(r for r in rows if r['id']=='study')
    row['steps']=[{'kind':'briefing','resource':'local','identity':''}]
    h.routines.save(row,revision)
    permissions=PermissionStore(tmp_path/'permissions.json', (tmp_path,))
    h.tool_executor.permissions=permissions
    permissions.set_rule('tool:local_briefing_memory','use',mode,expected_revision=permissions.read()[1])
    if mode=='block':
        token=h.routines.preview('study')['token'];h.routines.start(token)
        assert tool.calls==0
    else:
        h.processar_mensagem('modo estudo',manage_runtime=False);context=h.current_confirmation_context()
        assert context and tool.calls==0
        permissions.set_rule('tool:local_briefing_memory','use','block',expected_revision=permissions.read()[1])
        h.processar_mensagem('sim',manage_runtime=False,confirmation_session_id=context[0],confirmation_challenge_id=context[1])
        assert tool.calls==0 and h.current_confirmation_context() is None


def test_cancel_waiting_releases_active_run_and_session(tmp_path):
    h,tool=configured(tmp_path);tool.risk=RiskLevel.DESTRUCTIVE
    h.processar_mensagem('modo estudo',manage_runtime=False)
    h.cancel_pending_confirmation()
    assert h.routines.active is None and h.current_confirmation_context() is None and tool.calls==0


def test_tampered_confirmation_arguments_never_execute(tmp_path):
    h,tool=configured(tmp_path);tool.risk=RiskLevel.DESTRUCTIVE
    h.processar_mensagem('modo estudo',manage_runtime=False);context=h.current_confirmation_context()
    invocation=h.confirmation_sessions.current().invocation
    object.__setattr__(invocation,'tool_name','local_media_control')
    h.processar_mensagem('sim',manage_runtime=False,confirmation_session_id=context[0],confirmation_challenge_id=context[1])
    assert tool.calls==0
