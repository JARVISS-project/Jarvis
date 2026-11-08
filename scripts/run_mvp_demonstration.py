"""Run the MVP demonstration through real dispatchers and fake effect boundaries."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import multiprocessing as mp
from pathlib import Path
import re
import socket
from unittest.mock import patch

from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.llm.base import LLMClient
from jarvis.llm.types import LLMResponse
from jarvis.local_control.models import ApplicationInstance, ApplicationState
from jarvis.local_control.service import LocalControlService
from jarvis.media.models import (
    MediaCapabilities, MediaMetadata, MediaSnapshot, PlaybackState, SessionBinding,
)
from jarvis.media.service import GsmtcMediaService
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.managed import ManagedMemoryStore
from jarvis.memory.sqlite_store import SQLiteMemoryStore
from jarvis.organization.service import OrganizationService
from jarvis.organization.windows import WindowsMoves
from jarvis.runtime.diagnostic import run_diagnostics
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.safe_browser.models import ValidatedUri
from jarvis.safe_browser.service import SafeBrowserService
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.permissions import PermissionStore
from jarvis.security.safety_manager import SafetyManager
from jarvis.speech.gpu_budget import GpuBudgetManager, GpuMemory
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.get_datetime import GetDateTimeTool
from jarvis.tools.get_runtime_info import GetRuntimeInfoTool
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.organization import OrganizationExecuteTool, OrganizationUndoTool
from jarvis.tools.local_control import local_control_tools
from jarvis.tools.media import media_tools
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.safe_browser import safe_browser_tools
from jarvis.voice.semantic_shadow import load_catalog


class NoEffectLlm(LLMClient):
    def __init__(self):
        self.calls = 0

    def generate(self, messages, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return LLMResponse(content="Um exemplo simples ajuda a aprender.")


class FixtureProtector:
    """Fake OS encryption boundary for synthetic credentials, never production."""
    def protect(self, raw):
        return b'demo-fixture:' + raw

    def unprotect(self, raw):
        if not raw.startswith(b'demo-fixture:'):
            raise ValueError('Not a demo fixture')
        return raw[len(b'demo-fixture:'):]


class ObservedMoves(WindowsMoves):
    """Real native moves restricted to newly created synthetic files."""
    def __init__(self):
        super().__init__()
        self.calls = 0

    def move(self, *args, **kwargs):
        self.calls += 1
        return super().move(*args, **kwargs)


class FakeLocalBackend:
    def __init__(self) -> None:
        self.states = {name: ApplicationState(name, ()) for name in (
            "spotify", "obsidian", "default_browser",
        )}
        self.calls: list[tuple[object, ...]] = []
        self.level = 40
        self.muted = False

    def available_applications(self): return frozenset(self.states)
    def inspect_application(self, app): return self.states[app]
    def open_application(self, app):
        self.calls.append(("open", app))
        self.states[app] = ApplicationState(
            app, (ApplicationInstance(app, len(self.calls) + 100, len(self.calls) + 200, "Fake"),),
        )
    def focus_application(self, app, handle): self.calls.append(("focus", app, handle))
    def request_close(self, app, handle):
        self.calls.append(("close", app, handle)); self.states[app] = ApplicationState(app, ())
    def media_available(self): return True
    def control_media(self, action): self.calls.append(("legacy_media", action))
    def volume_available(self): return True
    def get_volume(self): return self.level, self.muted
    def set_volume(self, percent): self.calls.append(("volume", percent)); self.level = percent
    def set_muted(self, muted): self.calls.append(("mute", muted)); self.muted = muted


class FakeMediaAdapter:
    def __init__(self) -> None:
        self.session = object(); self.state = PlaybackState.PLAYING
        self.calls: list[str] = []
        self.controls = MediaCapabilities(True, True, True, True)
        self.properties = MediaMetadata("fake", "Demo", "JARVIS", timedelta(), timedelta(minutes=1))
    def available(self): return True
    def discover_manager(self, cancel): return object()
    def current_session(self, manager, cancel): return self.session
    def bind_session(self, session, cancel):
        return SessionBinding("fake-session", session, MediaSnapshot(self.state, self.controls, self.properties))
    def same_session(self, expected, current): return expected.handle is current
    def playback_state(self, session, cancel): return self.state
    def capabilities(self, session, cancel): return self.controls
    def metadata(self, session, cancel): return self.properties
    def execute(self, session, action, cancel):
        self.calls.append(action)
        if action == "pause": self.state = PlaybackState.PAUSED
        elif action == "play": self.state = PlaybackState.PLAYING
        return True


class FakeBrowserLauncher:
    def __init__(self) -> None: self.calls: list[str] = []
    def association_token(self): return "fake-association"
    def launch(self, request: ValidatedUri) -> bool:
        self.calls.append(request.canonical_url); return True


def _catalog() -> dict:
    catalog = deepcopy(load_catalog())
    wanted = {
        "get_datetime", "get_runtime_info", "local_application_open",
        "local_application_close", "local_volume_control", "local_media_control",
        "safe_browser_open", "safe_web_search",
    }
    for row in catalog["capabilities"]:
        if row["id"] in wanted:
            row["readiness_proven"] = True; row["implementation_proven"] = True
            row["state"] = "implemented_tested"
    return catalog


def build_demo(root: Path):
    config = JarvisConfig(settings_path=root / "missing.json", database_path=root / 'memory.db',
        audit_log_path=root / 'audit.log', skill_store_path=root / 'skills.json',
        filesystem_base_directory=root, obsidian_enabled=False, skills_obsidian_mirror_enabled=False)
    config.semantic_read_tools_enabled = True; config.local_control_enabled = True
    config.gsmtc_enabled = True; config.safe_browser_enabled = True
    config.stt_profile = "large-v3"; config.stt_model_normal = "large-v3"
    config.stt_device_normal = "cuda"; config.stt_compute_type_normal = "float16"
    config.stt_execution_mode = "process"
    resources = ResourceManager(); registry = ToolRegistry()
    registry.register(GetDateTimeTool(clock=lambda: datetime(
        2026, 9, 29, 12, 34, tzinfo=timezone(timedelta(hours=-3)),
    )))
    registry.register(GetRuntimeInfoTool(config, resources))
    local = FakeLocalBackend(); media = FakeMediaAdapter(); browser = FakeBrowserLauncher()
    for tool in local_control_tools(LocalControlService(local, sleep=lambda _delay: None)):
        registry.register(tool)
    for tool in media_tools(GsmtcMediaService(media)): registry.register(tool)
    for tool in safe_browser_tools(SafeBrowserService(browser)): registry.register(tool)
    permissions = PermissionStore(root / 'permissions.json', (root,))
    registry.register(ReadFileTool(FileSystemGuard(root, permissions)))
    moves = ObservedMoves()
    organization = OrganizationService(root / 'organization', permissions, backend=moves)
    registry.register(OrganizationExecuteTool(organization))
    registry.register(OrganizationUndoTool(organization))
    policy = CapabilityPolicy({c: True for c in (
        Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE, Capability.LOCAL_CONTROL,
        Capability.MEDIA_CONTROL, Capability.SAFE_BROWSER, Capability.LOCAL_BRIEFING_READ,
    )}, administrative=permissions)
    store = ManagedMemoryStore(SQLiteMemoryStore(config.database_path), permissions)
    audit = root / "audit.log"
    harness = Harness(
        llm=NoEffectLlm(), tool_registry=registry,
        tool_executor=ToolExecutor(SafetyManager(), AuditLogger(audit), policy, permissions=permissions),
        memory=ConversationMemory(), memory_store=store, config=config, resource_manager=resources,
        organization=organization,
    )
    catalog = _catalog()
    harness.intent_coordinator.semantic_read_dispatcher._catalog = catalog
    harness.intent_coordinator.local_control_dispatcher._catalog = catalog
    harness.intent_coordinator.safe_browser_dispatcher._catalog = catalog
    return harness, local, media, browser, audit


def run(output: Path, work_dir: Path) -> dict:
    # Refuse preexisting work files, including previous runs. Never inspect personal data.
    if work_dir.exists() and any(work_dir.iterdir()):
        raise ValueError('Use uma pasta de trabalho nova e vazia para a demonstração.')
    work_dir.mkdir(parents=True, exist_ok=True)
    # All HTTP in this runner uses a mock transport. Unexpected network is a failure.
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Live network forbidden in demo')):
        return _run(output, work_dir)


def _run(output: Path, work_dir: Path) -> dict:
    before_children = {child.pid for child in mp.active_children()}
    harness, local, media, browser, audit = build_demo(work_dir)
    organization = harness.administration.organization
    external_calls = []
    from jarvis.application.delegation import DelegationService
    from jarvis.integrations.text_providers import TextProviderAdapter
    import httpx

    def external_response(request):
        external_calls.append(request)
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP',
            'content': {'parts': [{'text': 'Resposta simulada, sem efeito externo.'}]}}]})

    harness.delegation = harness.administration.delegation = DelegationService(
        harness, FixtureProtector(), TextProviderAdapter(httpx.MockTransport(external_response)))
    rows: list[dict[str, object]] = []

    def effects() -> int:
        return (len(local.calls) + len(media.calls) + len(browser.calls) + organization.backend.calls
                + len(external_calls) + len(harness.memory_store.get_all()))
    def audit_tools() -> list[str]:
        if not audit.exists(): return []
        return re.findall(r"tool=([^ ]+)", audit.read_text(encoding="utf-8"))
    def observe(name: str, command: str, invoke, expected_effects: int,
                *, confirmation: bool = False, check=None, evidence='simulated') -> object:
        effect_before, tools_before = effects(), len(audit_tools())
        observed = invoke()
        tool_names = audit_tools()[tools_before:]
        effect_count = effects() - effect_before
        passed = effect_count == expected_effects and (check(observed) if check else True)
        display = observed
        if isinstance(display, tuple):
            display = display[-1] if isinstance(display[-1], str) else display[0]
        if hasattr(display, 'content'):
            display = display.content
        if not isinstance(display, str):
            display = 'Resultado estruturado validado; consulte as verificações do runner.'
        rows.append({
            "step": len(rows) + 1, "name": name, "command": command,
            "decision": display.replace(str(work_dir), '<demo>').replace(work_dir.as_posix(), '<demo>'),
            "tool": tool_names[-1] if tool_names else None,
            "confirmation_required": confirmation,
            "execution_count": effect_count,
            "simulated_effect": effect_count > 0 and evidence == 'simulated',
            "evidence": evidence,
            "expected": {"effect_executions": expected_effects},
            "observed": {"effect_executions": effect_count}, "passed": passed,
        })
        if not passed:
            raise AssertionError(f"{name}: effects={effect_count}, expected={expected_effects}; observed={observed}")
        return observed

    try:
        with patch("jarvis.runtime.diagnostic.check_gpu", return_value={"status": "ready"}), \
             patch("jarvis.runtime.diagnostic._installed", return_value=True), \
             patch("jarvis.runtime.diagnostic.check_ollama", return_value={"status": "ready"}), \
             patch("jarvis.runtime.diagnostic.check_browser", return_value={"status": "ready"}), \
             patch("jarvis.runtime.diagnostic.check_gsmtc", return_value={"status": "ready"}), \
             patch("jarvis.runtime.diagnostic.check_core_audio", return_value={"status": "ready"}), \
             patch("jarvis.runtime.diagnostic.check_apps", return_value={}):
            diagnostic = run_diagnostics(
                harness.config, GpuBudgetManager(lambda: GpuMemory(12288, 4000, 8288)),
            )
        observe("diagnóstico", "jarvis --doctor", lambda: diagnostic["stt"]["status"], 0)
        observe("hora", "Que horas são?", lambda: harness.processar_mensagem("Que horas são?"), 0)
        observe("data", "Qual é a data de hoje?", lambda: harness.processar_mensagem("Qual é a data de hoje?"), 0)
        observe("runtime", "Qual é o status do runtime?", lambda: harness.processar_mensagem("Qual é o status do runtime?"), 0)
        observe("abrir Spotify", "Abra o Spotify.", lambda: harness.processar_mensagem("Abra o Spotify."), 1)
        observe("abertura duplicada", "Abra o Spotify.", lambda: harness.processar_mensagem("Abra o Spotify."), 0)
        observe("abrir Obsidian", "Abra o Obsidian.", lambda: harness.processar_mensagem("Abra o Obsidian."), 1)
        observe("negação de fechamento", "Não feche o Spotify.", lambda: harness.processar_mensagem("Não feche o Spotify."), 0)
        observe("cancelar fechamento", "Feche o Spotify. / não", lambda: _cancel(harness, "Feche o Spotify."), 0, confirmation=True)
        confirmed = observe("confirmar fechamento", "Feche o Spotify. / sim", lambda: _confirm(harness, "Feche o Spotify."), 1, confirmation=True)
        close_session = confirmed[1]
        observe("volume", "Defina o volume para cinquenta por cento.", lambda: harness.processar_mensagem("Defina o volume para cinquenta por cento."), 1)
        observe("mute/unmute", "Ative o mudo. / Desative o mudo.", lambda: (
            harness.processar_mensagem("Ative o mudo."), harness.processar_mensagem("Desative o mudo.")), 2)
        observe("mídia", "Pause / reproduza / próxima / anterior", lambda: tuple(
            harness.processar_mensagem(command) for command in (
                "Pause a música.", "Reproduza a música.", "Próxima faixa.", "Faixa anterior.",
            )), 4)
        observe("navegador seguro", "Abra o GitHub.", lambda: harness.processar_mensagem("Abra o GitHub."), 1)
        observe("busca cancelada", "Pesquise no Google por JARVIS MVP. / não", lambda: _cancel(harness, "Pesquise no Google por JARVIS MVP."), 0, confirmation=True)
        observe("busca confirmada", "Pesquise no Google por JARVIS MVP. / sim", lambda: _confirm(harness, "Pesquise no Google por JARVIS MVP."), 1, confirmation=True)
        observe("bloquear CMD", "Abra o CMD.", lambda: harness.processar_mensagem("Abra o CMD."), 0)
        observe("bloquear duas intenções", "Abra Spotify e Obsidian.", lambda: harness.processar_mensagem("Abra Spotify e Obsidian."), 0)
        observe("bloquear replay", "sim (sessão consumida)", lambda: harness.processar_mensagem(
            "sim", confirmation_session_id=close_session.id,
            confirmation_challenge_id=close_session.challenge_id,
        ), 0, confirmation=True)
        observe('conversa local', 'Explique um assunto simples.',
                lambda: harness.processar_mensagem('Explique um assunto simples.'), 0,
                check=lambda value: value == 'Um exemplo simples ajuda a aprender.' and harness.llm.calls > 0)
        fixture = work_dir / 'neutral.txt'
        fixture.write_text('Documento sintético de demonstração.', encoding='utf-8')
        read = harness.tool_registry.get('read_file')
        observe('leitura local', 'Ler somente o documento sintético',
                lambda: harness._execute_tool(read, {'path': 'neutral.txt'}), 0,
                check=lambda r: r.status == 'success' and 'sintético' in r.content, evidence='temporary-files-real')
        observe('leitura bloqueada', 'Tentar ler fora da raiz autorizada',
                lambda: harness._execute_tool(read, {'path': '../outside.txt'}), 0,
                check=lambda r: r.status != 'success')

        def set_mode(resource, mode):
            p = harness.tool_executor.permissions
            p.set_rule(resource, 'use', mode, expected_revision=p.read()[1])

        # Expire through the real manager and its injectable clock; no sleep or gate changes.
        def expire_close():
            harness.processar_mensagem('Feche o Obsidian.')
            session = harness.confirmation_sessions.current()
            assert session
            with patch.object(harness.confirmation_sessions, 'clock', return_value=session.expires_at + 1):
                return harness.processar_mensagem('sim', confirmation_session_id=session.id,
                    confirmation_challenge_id=session.challenge_id)
        observe('confirmação expirada', 'Fechar Obsidian após expiração', expire_close, 0,
                confirmation=True, check=lambda r: 'não está mais ativa' in r)

        def revoked_close():
            harness.processar_mensagem('Feche o Obsidian.')
            session = harness.confirmation_sessions.current()
            assert session
            set_mode('tool:local_application_close', 'block')
            return harness.processar_mensagem('sim', confirmation_session_id=session.id,
                confirmation_challenge_id=session.challenge_id)
        observe('permissão revogada', 'Revogar fechamento antes de confirmar', revoked_close, 0,
                confirmation=True)
        set_mode('tool:local_application_close', 'allow')

        from jarvis.application.administration import MemoryDraft
        observe('memória explícita', 'Salvar preferência sintética local', lambda:
            harness.administration.save_memory(MemoryDraft('Prefiro exemplos curtos.', 'preferência', True, False, None)),
            1, evidence='temporary-files-real', check=lambda r: r['total'] == 1)
        set_mode('memory.save', 'block')
        def blocked_memory():
            try:
                harness.administration.save_memory(MemoryDraft('Não salvar.', 'fato', True, False, None))
            except PermissionError:
                return 'bloqueada'
            return 'unexpected success'
        observe('memória bloqueada', 'Tentar salvar memória sem acesso', blocked_memory, 0,
                check=lambda r: r == 'bloqueada')

        from jarvis.application.routines import digest
        rows_config = harness.routines.definitions()
        routine = deepcopy(rows_config[1])
        routine.update(enabled=True, steps=[{'kind': 'destination', 'resource': 'github', 'identity': ''}])
        harness.routines.save(routine, digest(rows_config))
        observe('prévia de rotina', 'Preparar modo estudo', lambda: harness.routines.preview('study'), 0,
                check=lambda p: p['ready'])
        observe('rotina executada', 'modo estudo', lambda: harness.processar_mensagem('modo estudo'), 1,
                check=lambda r: 'concluída' in r)
        set_mode('tool:safe_browser_open', 'ask')
        observe('rotina cancelada', 'modo estudo / não', lambda: _cancel(harness, 'modo estudo'), 0,
                confirmation=True, check=lambda _: harness.routines.active is None)
        observe('rotina expirada', 'modo estudo / expirar', lambda: _expire(harness, 'modo estudo'), 0,
                confirmation=True, check=lambda _: harness.routines.active is None)
        def revoke_routine():
            harness.processar_mensagem('modo estudo')
            context = harness.current_confirmation_context()
            assert context
            set_mode('tool:safe_browser_open', 'block')
            return harness.processar_mensagem('sim', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
        observe('rotina revogada', 'Revogar etapa pendente', revoke_routine, 0, confirmation=True,
                check=lambda _: harness.routines.active is None)
        observe('rotina bloqueada', 'modo estudo sem acesso', lambda: harness.processar_mensagem('modo estudo'), 0,
                check=lambda r: 'concluída' not in r)
        set_mode('tool:safe_browser_open', 'allow')
        # One step/one native move; only files created under this fresh work directory.
        batch_root = work_dir / 'synthetic-files'
        batch_root.mkdir()
        (batch_root / 'report.txt').write_bytes(b'synthetic contents')
        selected = organization.select(str(batch_root.resolve()))['selected']
        for name in ('organization_execute', 'organization_undo'):
            organization.grant(selected, name, 'allow', organization.permissions.read()[1])
        plan = observe('organização prévia', 'Analisar pasta sintética', lambda: organization.analyze(selected), 0,
                       check=lambda p: len(p['operations']) == 1, evidence='temporary-files-real')
        tool = harness.tool_registry.get('organization_execute')
        observe('organização executada', 'Executar plano revisado', lambda:
                harness._execute_tool(tool, {'plan': plan['id']}), 1, evidence='temporary-files-real',
                check=lambda r: r.status == 'success' and json.loads(r.content)['completed'] == 1 and
                (batch_root / 'Documentos/report.txt').read_bytes() == b'synthetic contents')
        observe('organização replay', 'Não repetir movimento do mesmo plano', lambda:
                harness._execute_tool(tool, {'plan': plan['id']}), 0, evidence='temporary-files-real')
        observe('organização desfazer', 'Desfazer plano comprovado', lambda:
                harness._execute_tool(harness.tool_registry.get('organization_undo'), {'plan': plan['id']}),
                1, evidence='temporary-files-real', check=lambda r: r.status == 'success' and
                (batch_root / 'report.txt').read_bytes() == b'synthetic contents')
        organization.grant(selected, 'organization_execute', 'ask', organization.permissions.read()[1])
        def organization_command():
            return '/organization execute ' + organization.analyze(selected)['id']
        observe('organização cancelada', 'Plano sintético / não', lambda:
                _cancel(harness, organization_command()), 0, confirmation=True)
        observe('organização expirada', 'Plano sintético / expirar', lambda:
                _expire(harness, organization_command()), 0, confirmation=True)
        def revoke_organization():
            harness.processar_mensagem(organization_command())
            context = harness.current_confirmation_context()
            assert context
            organization.grant(selected, 'organization_execute', 'block', organization.permissions.read()[1])
            return harness.processar_mensagem('sim', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
        observe('organização revogada', 'Revogar plano pendente', revoke_organization, 0, confirmation=True)
        observe('organização bloqueada', 'Executar sem acesso', lambda:
                harness.processar_mensagem(organization_command()), 0,
                check=lambda r: 'bloqueada' in r)

        service = harness.delegation
        service.configure('gemini', 'gemini-3.5-flash-lite', 4096, 128, 5, 'synthetic-demo-key')
        for resource in ('model.external', 'delegation.execute'):
            set_mode(resource, 'allow')
        observe('delegação cancelada', 'delegar gemini: Tarefa neutra / não', lambda:
                _cancel(harness, 'delegar gemini: Tarefa neutra'), 0, confirmation=True)
        observe('delegação expirada', 'delegar gemini: Tarefa neutra / expirar', lambda:
                _expire(harness, 'delegar gemini: Tarefa neutra'), 0, confirmation=True)
        def revoke_external():
            harness.processar_mensagem('delegar gemini: Tarefa neutra')
            context = harness.current_confirmation_context()
            assert context
            set_mode('delegation.execute', 'block')
            return harness.processar_mensagem('sim', confirmation_session_id=context[0], confirmation_challenge_id=context[1])
        observe('delegação revogada', 'Revogar envio antes de confirmar', revoke_external, 0, confirmation=True)
        observe('delegação bloqueada', 'delegar gemini: Tarefa neutra', lambda:
                harness.processar_mensagem('delegar gemini: Tarefa neutra'), 0,
                check=lambda r: 'concluída' not in r)
        set_mode('delegation.execute', 'allow')
        observe('delegação confirmada offline', 'delegar gemini: Tarefa neutra / sim', lambda:
                _confirm(harness, 'delegar gemini: Tarefa neutra'), 1, confirmation=True,
                check=lambda r: 'Delegação concluída' in r[0] and service.records[-1]['state'] == 'completed')
        assert len(external_calls) == 1
        assert 'Prefiro exemplos' not in external_calls[0].content.decode()
        harness.close(); harness.resource_manager.idle()
        after_children = {child.pid for child in mp.active_children()}
        observe("shutdown limpo", "encerrar demonstração", lambda: "zero residual", 0)
        if not after_children <= before_children:
            raise AssertionError(f"residual children: {after_children - before_children}")
    finally:
        harness.close()
    payload = {
        "kind": "observed_mvp_demonstration", "safe_fake_boundaries": True,
        "counts": {"passed": sum(bool(row["passed"]) for row in rows), "total": len(rows)},
        "zero_child_residual": True, "steps": rows,
        "external_network_calls": 0,
        "prior_real_validation": {'sprint': '23BU', 'source': 'sprint-23bu-evidence.json',
                                  'repeated': False, 'scope': 'neutral text only; historical evidence'},
        "limits": 'Apps/media/browser/LLM/HTTP/protection simulated; organization and memory use only new synthetic temporary files. No microphone, personal files or accounts.',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _cancel(harness: Harness, command: str):
    prompt = harness.processar_mensagem(command); session = harness.confirmation_sessions.current()
    assert session is not None
    response = harness.processar_mensagem(
        "não", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    assert harness.confirmation_sessions.current() is None
    return prompt, response


def _expire(harness: Harness, command: str):
    harness.processar_mensagem(command)
    session = harness.confirmation_sessions.current()
    assert session
    with patch.object(harness.confirmation_sessions, 'clock', return_value=session.expires_at + 1):
        return harness.processar_mensagem('sim', confirmation_session_id=session.id,
            confirmation_challenge_id=session.challenge_id)


def _confirm(harness: Harness, command: str):
    prompt = harness.processar_mensagem(command); session = harness.confirmation_sessions.current()
    assert session is not None
    response = harness.processar_mensagem(
        "sim", confirmation_session_id=session.id,
        confirmation_challenge_id=session.challenge_id,
    )
    return response, session, prompt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/mvp-demonstration-results.json"))
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = run(args.output, args.work_dir)
    print(json.dumps(payload["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
