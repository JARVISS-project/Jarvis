import socket

import pytest

from jarvis.security.capabilities import Capability
from tests.integration.test_administration_23bh import build, change


def availability(service):
    return {r['name']: r for r in service.dispatch('help.list', {})['availability']}


def test_dashboard_reads_local_evidence_without_loading_probing_or_executing(tmp_path, monkeypatch):
    h, service = build(tmp_path)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail('Dashboard initiated an effect, model, audio or probe')
        monkeypatch.setattr(socket.socket, 'connect', forbidden)
        monkeypatch.setattr(h.tool_executor, 'execute', forbidden)
        monkeypatch.setattr(h.llm, 'generate', forbidden)
        monkeypatch.setattr(service.first_use, 'probe', forbidden)
        service.config.settings_path.write_text('{}')
        before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        rows = availability(service)
        assert set(rows) == {'Assistente local', 'Voz', 'Gemini', 'Google — leituras',
                             'Google — escrita na agenda', 'Aplicativos', 'Mídia'}
        assert rows['Assistente local']['status'] == 'não verificado'
        assert rows['Gemini']['status'] == 'sem permissão'
        assert rows['Google — escrita na agenda']['status'] == 'desligado'
        assert all(r['reason'] and r['page'] and r['next_action'] for r in rows.values())
        assert {p: p.read_bytes() for p in before} == before
        assert {p for p in tmp_path.rglob('*') if p.is_file()} == set(before)
        assert not service.config.google_calendar_write_enabled
    finally:
        h.close()


def test_credential_presence_never_implies_external_availability(tmp_path):
    from scripts.run_mvp_demonstration import FixtureProtector
    from jarvis.application.delegation import DelegationService
    h, service = build(tmp_path)
    try:
        delegation = DelegationService(h, FixtureProtector())
        service.delegation = delegation
        for resource in ('model.external', 'delegation.execute'):
            change(service, resource, 'allow')
        assert availability(service)['Gemini']['status'] == 'não configurado'
        delegation.configure('gemini', 'gemini-3.5-flash-lite', 4096, 128, 5, 'synthetic-fixture')
        row = availability(service)['Gemini']
        assert row['status'] == 'não verificado' and 'não verificados' in row['reason']
        change(service, 'delegation.execute', 'block')
        assert availability(service)['Gemini']['page'] == 'Permissões'
        assert not delegation.records
        change(service, 'delegation.execute', 'allow')
        delegation.path.write_bytes(b'invalid protected fixture')
        row = availability(service)['Gemini']
        assert row['status'] == 'indisponível' and 'Armazenamento protegido' in row['reason']
    finally:
        h.close()


@pytest.mark.parametrize('mode', ['missing', 'disabled', 'blocked', 'unverified'])
def test_media_distinguishes_registration_configuration_permission_and_live_state(tmp_path, mode):
    from scripts.run_mvp_demonstration import FakeMediaAdapter
    from jarvis.media.service import GsmtcMediaService
    from jarvis.tools.media import media_tools
    h, service = build(tmp_path)
    try:
        service.config.gsmtc_enabled = mode != 'disabled'
        if mode != 'missing':
            for tool in media_tools(GsmtcMediaService(FakeMediaAdapter())):
                service.registry.register(tool)
        service.capability_policy._permissions[Capability.MEDIA_CONTROL] = True
        if mode == 'blocked':
            change(service, 'tool:local_media_control', 'block')
        expected = {'missing': 'não configurado', 'disabled': 'desligado',
                    'blocked': 'sem permissão', 'unverified': 'não verificado'}
        assert availability(service)['Mídia']['status'] == expected[mode]
    finally:
        h.close()


def test_latest_diagnostic_is_explicitly_historical_and_policy_reloads(tmp_path):
    h, service = build(tmp_path)
    try:
        service.first_use.latest = {'assistant': {'status': 'ready'}}
        assert availability(service)['Assistente local']['status'] == 'pronto'
        assert 'Último diagnóstico' in availability(service)['Assistente local']['reason']
        change(service, 'microphone.capture', 'block')
        assert availability(service)['Voz']['status'] == 'sem permissão'
        service.permissions.path.write_text('{invalid')
        assert availability(service)['Gemini']['status'] == 'sem permissão'
    finally:
        h.close()
