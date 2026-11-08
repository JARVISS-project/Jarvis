"""Batch Tools accept a sealed plan ID, never filesystem paths from an LLM."""
from __future__ import annotations

from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


class OrganizationExecuteTool(Tool):
    name = 'organization_execute'
    description = 'Organiza somente plano local revisado, dentro da raiz e regras explicitamente autorizadas.'
    risk = RiskLevel.REVERSIBLE_LOCAL
    parameters = {'type': 'object', 'properties': {'plan': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}}, 'required': ['plan'], 'additionalProperties': False}
    permission_effect_per_operation = True
    undo = False

    def __init__(self, service):
        self.service = service

    def permission_decision(self, arguments):
        plan = self.service.load(arguments.get('plan'))
        decision = self.service.decision(self.name, plan['root'])
        _, root, _ = self.service.root(plan['root'])
        modes = {decision.mode}
        for row in plan['operations']:
            modes.update(self.service.permissions.path_decision(root / path, 'write') for path in (row['source'], row['destination']))
        from jarvis.security.permissions import PermissionDecision
        return PermissionDecision('block' if 'block' in modes else 'ask' if 'ask' in modes else 'allow', decision.revision)

    def prepare(self, **kwargs):
        prepared = self.service.prepare(kwargs['plan'], self.undo)
        plan = self.service.load(kwargs['plan'])
        _, root, value = self.service.root(plan['root'])
        target = f"{'Desfazer' if self.undo else 'Organizar'} {root}; {len(plan['operations'])} operações; regras {prepared['binding'][:12]}. Sem excluir/sobrescrever/enviar conteúdo. Revise a prévia na UI."
        target += '\n' + '\n'.join(f"{row['destination'] if self.undo else row['source']} → {row['source'] if self.undo else row['destination']}; {row['reason']}" for row in plan['operations'])
        return ToolPreparation(target, prepared, prepared['binding'])

    def get_target(self, **kwargs):
        return self.prepare(**kwargs).target

    def preflight_prepared(self, preparation, **kwargs):
        if not isinstance(preparation.context, dict) or preparation.context.get('id') != kwargs.get('plan') or preparation.context.get('undo') != self.undo or preparation.binding_digest != preparation.context.get('binding'):
            raise PermissionError('Plano/preparo diferente da operação autorizada.')
        self.service.revalidate(preparation.context)

    def refresh_prepared(self, preparation, **kwargs):
        self.preflight_prepared(preparation, **kwargs)
        return preparation

    def execute_prepared(self, preparation, **kwargs):
        self.preflight_prepared(preparation, **kwargs)
        return self.service.execute(preparation.context, self)

    def execute(self, **kwargs):
        raise PermissionError('Use plano preparado e executor existente.')


class OrganizationUndoTool(OrganizationExecuteTool):
    name = 'organization_undo'
    description = 'Desfaz somente movimentos comprovados do JARVIS, sem sobrescrever o caminho original.'
    risk = RiskLevel.REVERSIBLE_LOCAL
    parameters = OrganizationExecuteTool.parameters
    undo = True
