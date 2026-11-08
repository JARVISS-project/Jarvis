"""Catalog Tools accept IDs/references, never executables or free arguments."""
from __future__ import annotations

from threading import RLock

from jarvis.desktop.application_control import PreparedCatalogAction
from jarvis.security.authorization import AuthorizationReceipt
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolPreparation


class CatalogApplicationTool(Tool):
    def __init__(self, service, action: str):
        self.service, self.action = service, action
        self.name = 'app_catalog_' + action
        self.description = 'Opera somente identidade descoberta e autorizada; não aceita caminhos, URLs ou argumentos.'
        self.risk = RiskLevel.SENSITIVE if action == 'close' else RiskLevel.READ_ONLY if action == 'status' else RiskLevel.REVERSIBLE_LOCAL
        self.requires_authorization_receipt = action == 'close'
        properties = {'application': {'type': 'string', 'pattern': '^app-[a-f0-9]{32}$'},
            'reference': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}}
        required = ['application']
        if action == 'window':
            properties['action'] = {'type': 'string', 'enum': ['focus', 'minimize', 'maximize', 'restore', 'place']}
            required.append('action')
        self.parameters = {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
        self._approved = {}
        self._lock = RLock()

    def permission_decision(self, arguments):
        return self.service.permission_decision(arguments.get('application'), arguments.get('action') if self.action == 'window' else self.action)

    def prepare(self, **kwargs):
        action = kwargs.get('action') if self.action == 'window' else self.action
        context = self.service.prepare(kwargs['application'], action, kwargs.get('reference'))
        target = f'Aplicativo: {context.entry.name}; ação: {action}; identidade: {context.entry.id}'
        if context.instance:
            target += f'; PID: {context.instance.pid}; HWND: {context.instance.hwnd}'
        return ToolPreparation(target, context, context.digest())

    def get_target(self, **kwargs):
        return self.prepare(**kwargs).target

    def preflight_prepared(self, preparation, **kwargs):
        if not isinstance(preparation.context, PreparedCatalogAction) or preparation.context.digest() != preparation.binding_digest:
            raise PermissionError('Preparo inválido.')
        action = kwargs.get('action') if self.action == 'window' else self.action
        if kwargs.get('application') != preparation.context.entry.id or action != preparation.context.action:
            raise PermissionError('Payload diferente da operação preparada.')
        if kwargs.get('reference') is not None:
            selected = self.service._references.get(kwargs['reference'])
            if selected is None or selected[2] != preparation.context.instance:
                raise PermissionError('Referência diferente da janela preparada.')
        self.service.revalidate(preparation.context)

    def refresh_prepared(self, preparation, **kwargs):
        self.preflight_prepared(preparation, **kwargs)
        return preparation

    def bind_authorized(self, preparation, authorization=None):
        if self.action != 'close':
            return None
        if not isinstance(authorization, AuthorizationReceipt) or not authorization.claim(self.name, preparation.target, preparation.binding_digest):
            raise PermissionError('Fechamento exige confirmação vinculada à janela.')
        with self._lock:
            if len(self._approved) >= 64:
                raise PermissionError('Limite de preparações de fechamento.')
            self._approved[id(preparation)] = preparation
        return preparation

    def execute_authorized(self, preparation):
        with self._lock:
            if self._approved.pop(id(preparation), None) is not preparation:
                raise PermissionError('Confirmação ausente ou já utilizada.')
        return self.service.execute(preparation.context)

    def execute_prepared(self, preparation, **kwargs):
        if self.action == 'close':
            raise PermissionError('Fechamento exige recibo vinculado.')
        self.preflight_prepared(preparation, **kwargs)
        return self.service.execute(preparation.context)

    def execute(self, **kwargs):
        raise PermissionError('Use preparo e executor; nenhuma execução direta.')


class CatalogApplicationOpenTool(CatalogApplicationTool):
    name = 'app_catalog_open'
    risk = RiskLevel.REVERSIBLE_LOCAL
    description = 'Abre somente ID descoberto/autorizado e verifica uma janela compatível.'

    def __init__(self, service):
        super().__init__(service, 'open')


class CatalogApplicationStatusTool(CatalogApplicationTool):
    name = 'app_catalog_status'
    risk = RiskLevel.READ_ONLY
    description = 'Consulta instâncias da identidade autorizada, sem ler conteúdo do aplicativo.'

    def __init__(self, service):
        super().__init__(service, 'status')


class CatalogApplicationWindowTool(CatalogApplicationTool):
    name = 'app_catalog_window'
    risk = RiskLevel.REVERSIBLE_LOCAL
    description = 'Foca/minimiza/maximiza/restaura/posiciona somente janela validada e autorizada.'

    def __init__(self, service):
        super().__init__(service, 'window')


class CatalogApplicationCloseTool(CatalogApplicationTool):
    name = 'app_catalog_close'
    risk = RiskLevel.SENSITIVE
    description = 'Fecha graciosamente a janela selecionada após confirmação vinculada.'

    def __init__(self, service):
        super().__init__(service, 'close')


def application_catalog_tools(service):
    return [CatalogApplicationOpenTool(service), CatalogApplicationStatusTool(service),
        CatalogApplicationWindowTool(service), CatalogApplicationCloseTool(service)]
