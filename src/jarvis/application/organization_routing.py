"""Private deterministic organization commands; no provider receives a file plan."""
from __future__ import annotations

import json
import re

from jarvis.desktop.catalog import _normalize


def dispatch(harness, text):
    # Names/descriptions in existing Skill commands are not organization intents.
    # Skill execution still passes through the same executor and scoped policy.
    skills = harness.skill_coordinator
    if (text.strip().casefold().startswith(('/skill', '/skills'))
            or skills.natural_skill_learning_name(text) is not None
            or skills.natural_skill_run_name(text) is not None):
        return None
    normalized = _normalize(text)
    natural = {'organize minha pasta downloads': 'analyze', 'organize a pasta selecionada': 'analyze',
        'mostre o plano de organizacao': 'plan', 'cancele a organizacao': 'cancel',
        'desfaca a ultima organizacao': 'undo_latest'}
    match = re.fullmatch(r'/organization (execute|undo|plan) ([0-9a-f]{32})', text.strip())
    if match:
        action, plan_id = match.groups()
    elif normalized in natural:
        action, plan_id = natural[normalized], None
    elif text.strip().startswith('/organization') or re.search(r'\b(?:organize|organizacao)\b', normalized):
        return 'Pedido ambíguo/negado ou com argumentos extras; nenhuma organização executada. Use a pasta e a prévia da UI.'
    else:
        return None
    service = harness.organization
    if service is None:
        return 'Organização indisponível; nenhuma operação executada.'
    try:
        if action == 'cancel':
            return service.cancel()['message']
        if not harness.config.filesystem_read_enabled:
            return 'Leitura de metadados indisponível; nenhuma organização executada.'
        token, root, _ = service.root()
        if action == 'analyze':
            if 'downloads' in normalized and root.name.casefold() != 'downloads':
                return 'Escolha Downloads pelo seletor da UI e autorize o escopo; não uso outra pasta silenciosamente.'
            plan = service.analyze(token)
            return json.dumps({'plan': plan, 'message': 'Prévia sem movimentos. Revise e autorize pela UI ou confirmação do executor.'}, ensure_ascii=False)
        if plan_id is None:
            plans = [service.load(path.stem[5:]) for path in service.store.directory.glob('plan-*.json')]
            plans = [p for p in plans if p['root'] == token and (p['executed'] if action == 'undo_latest' else True)]
            if not plans:
                return 'Nenhum plano no escopo selecionado; analise pela UI.'
            plan_id = max(plans, key=lambda p: p['created'])['id']
        if action == 'plan':
            plan = service.view(plan_id)
            if plan['root'] != token:
                raise PermissionError('Plano pertence a outra seleção.')
            return json.dumps(plan, ensure_ascii=False)
        tool = harness.tool_registry.get('organization_undo' if action in {'undo', 'undo_latest'} else 'organization_execute')
        if tool is None:
            return 'Capability de organização indisponível; nenhuma operação executada.'
        return harness._execute_tool(tool, {'plan': plan_id}).content
    except (OSError, ValueError, PermissionError, RuntimeError):
        return 'Escolha/revise a pasta, regras, identidade e permissões na UI; nenhuma nova operação confirmada.'
