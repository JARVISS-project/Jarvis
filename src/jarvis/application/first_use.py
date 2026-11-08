"""Local help and progress. No providers, executors or operational grants."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from jarvis.security.capabilities import Capability, TOOL_CAPABILITIES
from jarvis.speech.profile import parse_stt_profile
from jarvis.runtime.hardware import hardware_report


STEPS = (
    ('welcome', 'Conhecer o JARVIS', 'Escolha como quer ser tratado, tom e tamanho das respostas em Ajustes. Converse por texto em Conversa. Informações verificadas e opiniões são diferentes.', 'Ajustes'),
    ('hardware', 'Conferir disponibilidade', 'Use Diagnosticar para conferir assistente e computador. Capacidade total e recursos livres agora são diferentes. Nenhum modelo será carregado.', ''),
    ('voice', 'Configurar voz', 'Em Ajustes, escolha entre vozes instaladas e ouça a prévia somente se quiser. Hey Jarvis ativa a conversa quando o runtime de voz está ligado e o microfone autorizado. Sem isso, use texto. Interromper fala para o áudio; Cancelar interrompe o pedido.', 'Ajustes'),
    ('permissions', 'Escolher permissões', 'Abra Permissões e escolha os acessos. Memória permite consultar, editar e usar sessão temporária: ela não alimenta memória permanente. Uma confirmação vale somente para a operação e alvo apresentados; Cancelar não aprova nada.', 'Permissões'),
    ('connections', 'Conexões opcionais', 'Contas e conexões mostra o que está configurado. Conta conectada prova acesso, mas não autoriza ações. Agenda READ e WRITE são separados. Conectar exige o fluxo oficial e sua interação; você pode continuar sem Google. Na aba OpenAI / Gemini, configure chave API e limites se desejar delegar texto. O assistente local continua padrão. Assinatura não comprova créditos API; permissões e confirmação de cada prévia são obrigatórias. Histórico, agenda, memória e documentos não são incluídos automaticamente.', 'Contas e conexões'),
    ('examples', 'Experimentar comandos', 'Pesquise exemplos abaixo; consultar não executa. Em Rotinas, escolha etapas/fontes/alvos e salve; modelos começam desabilitados. Confira a prévia antes de habilitar ou executar. Modo estudo e começar meu dia usam o mesmo executor e confirmação por etapa. Cancelar impede próximas etapas, sem desfazer efeitos. A Conversa mostra o histórico da sessão.', 'Conversa'),
)

# Literal examples, not executable commands. Availability is assessed separately.
EXAMPLES = (
    ('Conversa', '', 'Explique uma maneira simples de aprender um assunto.', 'Conversa'),
    ('Agenda', 'google_calendar_list_events', 'Quais eventos tenho hoje na agenda selecionada?', 'Contas e conexões'),
    ('Documentos selecionados', 'google_docs_read', 'Resuma o documento que selecionei.', 'Contas e conexões'),
    ('Documentos locais', 'read_file', 'Leia o documento na pasta autorizada que eu indicar.', 'Permissões'),
    ('Aplicativos', 'app_catalog_open', 'Abra um aplicativo que escolhi em Aplicativos.', 'Aplicativos'),
    ('Navegador', 'safe_browser_open', 'Abra um destino configurado no navegador.', 'Permissões'),
    ('Mídia', 'local_media_control', 'Pause a mídia da sessão atual.', 'Permissões'),
    ('Volume', 'local_volume_control', 'Defina o volume em 30 por cento.', 'Permissões'),
    ('Organização de arquivos', 'organization_execute', 'Organize a pasta selecionada conforme a prévia.', 'Organização de arquivos'),
    ('Briefing', 'local_briefing_memory', 'Resumo do dia.', 'Permissões'),
)


class FirstUseService:
    def __init__(self, administration, *, probe=hardware_report) -> None:
        self.admin = administration
        self.config = administration.config
        self.path = self.config.settings_path.with_name('onboarding.json')
        administration.permissions.protected_paths.add(self.path.resolve())
        self.probe = probe
        self.latest = None
        self._prepared = {}
        self._rollback = None

    def progress(self) -> dict:
        ids = [s[0] for s in STEPS]
        default = dict(version=1, step=ids[0], completed=[], dismissed=False, finished=False)
        if not self.path.exists():
            return {**default, 'auto_show': True, 'valid': True}
        try:
            if self.path.stat().st_size > 16384:
                raise ValueError
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or not isinstance(raw.get('completed', []), list):
                raise ValueError
            if any(type(raw.get(k, False)) is not bool for k in ('dismissed', 'finished')):
                raise ValueError
            completed = [s for s in ids if s in raw.get('completed', [])]
            step = raw.get('step') if raw.get('step') in ids else next((s for s in ids if s not in completed), ids[-1])
            return {**default, 'step': step, 'completed': completed,
                    'dismissed': raw.get('dismissed', False), 'finished': raw.get('finished', False),
                    'auto_show': not raw.get('dismissed', False) and not raw.get('finished', False), 'valid': True}
        except (ValueError, OSError, UnicodeError):
            return {**default, 'auto_show': False, 'valid': False}

    def update(self, action: str) -> dict:
        state = self.progress()
        if not state['valid']:
            raise ValueError('Progresso inválido; preserve o arquivo para diagnóstico.')
        if action not in {'next', 'back', 'skip', 'resume'}:
            raise ValueError('Navegação inválida.')
        ids = [s[0] for s in STEPS]
        index = ids.index(state['step'])
        if action == 'next':
            if state['step'] not in state['completed']:
                state['completed'].append(state['step'])
            state['finished'] = index == len(ids) - 1
            state['step'] = ids[min(index + 1, len(ids) - 1)]
        elif action == 'back':
            state['step'] = ids[max(index - 1, 0)]
        state['dismissed'] = action == 'skip'
        if action == 'resume':
            state['finished'] = False
        data = {k: state[k] for k in ('version', 'step', 'completed', 'dismissed', 'finished')}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name('.onboarding-' + uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return self.progress()

    def inventory(self) -> dict:
        policy = self.admin.inventory()
        rows = {r['resource']: r for r in policy['rows'] if r['action'] == 'use'}
        examples = []
        for category, tool_name, example, page in EXAMPLES:
            row = rows.get('tool:' + tool_name)
            if not tool_name:
                configured = True
                authorized = policy['valid']
                ready = bool(self.latest and self.latest['assistant']['status'] == 'ready')
            else:
                capability = TOOL_CAPABILITIES[tool_name]
                gate = self.admin.capability_policy
                configured = self.admin.registry.get(tool_name) is not None and bool(gate and gate.is_enabled(capability))
                authorized = bool(policy['valid'] and row and not row['effective'].startswith(('bloqueado', 'block', 'indisponível')))
                # No external probes here: authenticated/live targets must be checked by their official UI/Tool.
                ready = configured and authorized and tool_name == 'read_file'
                if tool_name == 'local_briefing_memory':
                    configured = bool(self.config.briefing_enabled and gate and gate.is_enabled(Capability.LOCAL_BRIEFING_READ))
                    authorized = policy['valid'] and self.admin.permissions.decision('tool:local_briefing_memory').mode != 'block'
                    ready = configured and authorized and not self.config.google_calendar_enabled
            status = 'disponível no escopo' if ready and authorized else 'precisa conferir disponibilidade' if configured and authorized else 'precisa configurar ou autorizar'
            examples.append(dict(category=category, example=example, status=status, available=ready and authorized,
                                 next_action=('Envie em Conversa; permissões, alvo e confirmação continuam obrigatórios.' if ready and authorized else 'Abra ' + page + ' para conferir configuração, alvo e acesso.'), page=page))
        return {'progress': self.progress(), 'steps': [dict(id=s[0], title=s[1], text=s[2], page=s[3]) for s in STEPS],
                'examples': examples, 'diagnostic': self.latest,
                'availability': self.availability(policy),
                'advanced': 'Ajuda usa registro de Tools, CapabilityPolicy e política efetiva. Prontidão externa não é sondada aqui. Autenticação não concede autorização. Nenhum exemplo executa ações.'}

    def availability(self, policy: dict) -> list[dict]:
        """Summarize local evidence only; never probe or execute a capability."""
        rows = {r['resource']: r for r in policy['rows'] if r['action'] == 'use'}
        result = []

        def add(name, status, reason, page, next_action):
            result.append(dict(name=name, status=status, reason=reason,
                               page=page, next_action=next_action))

        def blocked(resource):
            row = rows.get(resource)
            return not policy['valid'] or bool(row and (
                row['mode'] == 'block' or row['effective'].startswith(('bloqueado', 'block'))))

        assistant = (self.latest or {}).get('assistant', {})
        add('Assistente local', 'pronto' if assistant.get('status') == 'ready' else 'indisponível',
            'Último diagnóstico local favorável; resposta do modelo não testada pelo painel.'
            if assistant.get('status') == 'ready' else 'Disponibilidade ainda não comprovada pelo diagnóstico local.',
            'Ajuda', 'Use Diagnosticar computador em Ajuda; converse por texto em Conversa.')
        voice_enabled = self.config.wake_word_enabled or self.config.tts_enabled
        add('Voz', 'sem permissão' if blocked('microphone.capture') else
            'indisponível' if voice_enabled else 'desligado',
            'Captura não autorizada; resposta falada pode ser ajustada em Ajustes.' if blocked('microphone.capture') else
            'Voz configurada; microfone, escuta, modelos e saída de áudio não testados pelo painel.' if self.config.wake_word_enabled else
            'Resposta falada configurada; ativação por voz desligada. Áudio e modelos não testados pelo painel.' if self.config.tts_enabled else
            'Ativação por voz e resposta falada desligadas; texto continua disponível.',
            'Permissões' if blocked('microphone.capture') else 'Ajustes',
            'Confira o microfone em Permissões e a voz em Ajustes; consulte Ajuda para iniciar por voz.')
        delegation = getattr(self.admin, 'delegation', None)
        providers = delegation.inventory()['providers'] if delegation else []
        gemini = next((p for p in providers if p['provider'] == 'gemini'), {})
        unreadable = bool(delegation and delegation.path.exists() and
                          not any(p['configured'] for p in providers))
        denied = any(blocked(r) for r in ('model.external', 'delegation.execute', 'tool:external_text_delegate'))
        add('Gemini', 'sem permissão' if denied else 'indisponível' if gemini.get('configured') or unreadable else 'não configurado',
            'Envio externo bloqueado pela política local.' if denied else
            'Armazenamento protegido indisponível; revise a configuração API em Contas e conexões.' if unreadable else
            'Chave configurada localmente; disponibilidade, quota e plano não verificados.' if gemini.get('configured') else
            'Configure uma chave API; assinatura e conta Google não substituem essa configuração.',
            'Permissões' if denied else 'Contas e conexões',
            'Revise Modelos externos e Execução delegada em Permissões; prepare cada envio na aba OpenAI / Gemini de Contas e conexões.')
        for name, enabled, tools in (
            ('Google — leituras', self.config.google_calendar_enabled or self.config.google_docs_read_enabled or self.config.google_drive_read_enabled,
             ('google_calendar_list_events', 'google_docs_read', 'google_drive_find_files')),
            ('Google — escrita na agenda', self.config.google_calendar_write_enabled, ('google_calendar_create_event',)),
            ('Aplicativos', self.config.local_control_enabled, ('app_catalog_open', 'local_application_open')),
            ('Mídia', self.config.gsmtc_enabled, ('local_media_control',)),
        ):
            configured = [t for t in tools if self.admin.registry.get(t) is not None]
            allowed = [t for t in configured if not blocked('tool:' + t) and
                       self.admin.capability_policy and self.admin.capability_policy.is_enabled(TOOL_CAPABILITIES[t])]
            page = 'Contas e conexões' if name.startswith('Google') else 'Aplicativos' if name == 'Aplicativos' else 'Permissões'
            status = 'desligado' if not enabled else 'não configurado' if not configured else 'sem permissão' if not allowed else 'indisponível'
            reason = ('Recurso desligado na configuração atual.' if not enabled else
                      'Componente não registrado nesta sessão.' if not configured else
                      'Política local não permite esta capacidade.' if not allowed else
                      'Configuração presente; conta, alvo ou sessão atual não foram testados pelo painel.')
            add(name, status, reason, 'Permissões' if status == 'sem permissão' else page,
                'Confira conta e recurso selecionado em Contas e conexões; leituras e escrita têm acessos separados.'
                if name.startswith('Google') else 'Escolha o aplicativo em Aplicativos e confira seu acesso em Permissões.'
                if name == 'Aplicativos' else 'Abra a mídia no aplicativo e confira o acesso em Permissões; peça a ação em Conversa.')
        return result

    def diagnose(self) -> dict:
        self.latest = None
        self._prepared.clear()
        self.latest = self.probe(self.config)
        if self.admin.conversation_preferences is not None:
            voice = self.admin.conversation_preferences.inventory()
            self.latest['voice'] = {k: voice[k] for k in ('available', 'diagnostic', 'provider', 'speed_supported')}
        return self.latest

    def _revision(self) -> str:
        content = self.config.settings_path.read_bytes() if self.config.settings_path.exists() else b''
        return hashlib.sha256(content).hexdigest()

    def prepare_profile(self, profile: str, actor=None) -> dict:
        if not isinstance(profile, str):
            raise ValueError('Escolha um perfil instalado.')
        selected = parse_stt_profile(profile)
        if os.getenv('JARVIS_STT_PROFILE') is not None or os.getenv('JARVIS_STT_EXECUTION_MODE') is not None:
            raise PermissionError('Perfil definido pelo ambiente; use a configuração existente.')
        if not self.latest or profile not in {p['profile'] for p in self.latest['profiles']}:
            raise PermissionError('Execute o diagnóstico antes de escolher um perfil.')
        token = uuid4().hex
        self._prepared = {token: (actor, self._revision(), self.config.stt_profile, profile,
                                  datetime.now(timezone.utc) + timedelta(minutes=5))}
        return dict(token=token, current=self.config.stt_profile, proposed=profile,
                    detail=f'Somente perfil STT: {selected.model}/{selected.device}/{selected.compute_type}. Voz e modelo de conversa preservados. Gates não mudam. Reinicie o runtime para ativar; nenhum modelo é carregado agora. Modelo STT e bibliotecas precisam estar instalados: o backend existente pode baixar modelo ausente ao ser ativado. Prepare o cache antes de uso offline. Rollback disponível após aplicar.')

    def apply_profile(self, token: str, actor=None) -> dict:
        prepared = self._prepared.pop(token, None) if isinstance(token, str) else None
        if not prepared or prepared[0] != actor or prepared[1] != self._revision() or prepared[4] < datetime.now(timezone.utc) or os.getenv('JARVIS_STT_PROFILE') is not None or os.getenv('JARVIS_STT_EXECUTION_MODE') is not None:
            raise PermissionError('Prévia antiga ou de outra sessão.')
        self.config.save_stt_profile(prepared[3])
        self._rollback = (actor, self._revision(), prepared[2])
        return dict(profile=self.config.stt_profile, rollback_available=True, restart_required=True)

    def rollback_profile(self, actor=None) -> dict:
        previous = self._rollback
        if not previous or previous[0] != actor or previous[1] != self._revision():
            raise PermissionError('Perfil alterado depois da aplicação; prepare nova escolha.')
        self.config.save_stt_profile(previous[2])
        self._rollback = None
        return dict(profile=self.config.stt_profile, rollback_available=False, restart_required=True)
