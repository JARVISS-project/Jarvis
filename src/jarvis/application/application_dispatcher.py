"""Exact catalog commands reuse the existing local dispatcher's replay fencing."""
from __future__ import annotations

import re

from jarvis.application.local_control_dispatcher import (
    LocalControlDecision, LocalControlDispatcher, LocalControlIntent,
)
from jarvis.desktop.application_control import TOOLS
from jarvis.desktop.catalog import _normalize


class ApplicationNormalizer:
    def __init__(self, service):
        self.service = service

    def resolve(self, text):
        if text.startswith('/app'):
            match = re.fullmatch(r'/app (open|status|focus|minimize|maximize|restore|place|close) (app-[a-f0-9]{32})(?: ([a-f0-9]{32}))?', text.strip())
            if not match:
                return None, 'invalid_catalog_command', True
            action, application, reference = match.groups()
        else:
            normalized = _normalize(text)
            verbs = {'abra': 'open', 'abrir': 'open', 'abre': 'open', 'feche': 'close', 'fechar': 'close',
                'minimize': 'minimize', 'minimizar': 'minimize', 'maximize': 'maximize', 'maximizar': 'maximize',
                'restaure': 'restore', 'restaurar': 'restore', 'consulte': 'status'}
            tokens = normalized.split()
            if not any(t in tokens for t in [*verbs, 'traga', 'trazer', 'traz']):
                return None, 'not_application_command', False
            if any(t in tokens for t in ('nao', 'nunca', 'jamais', 'nem', 'sem')):
                return None, 'negated', True
            focus = re.fullmatch(r'(?:traga|trazer|traz) (?:o |a )?(.+) para (?:frente|primeiro plano)', normalized)
            normal = re.fullmatch(r'(' + '|'.join(verbs) + r') (?:o |a )?(.+)', normalized)
            if focus:
                action, name = 'focus', focus[1]
            elif normal:
                action, name = verbs[normal[1]], normal[2]
            else:
                return None, 'residual_or_multiple_intents', True
            name = re.sub(r'^janela (?:do|da|de) ', '', name)
            matches = self.service.catalog.match(name, self.service.settings.read()['aliases'])
            if len(matches) != 1:
                return None, 'ambiguous_or_undiscovered', True
            application, reference = matches[0].id, None
        arguments = {'application': application}
        if reference is not None:
            arguments['reference'] = reference
        group = self.service.group(action)
        if group == 'window':
            arguments['action'] = action
        return LocalControlIntent(TOOLS[group], arguments), 'resolved', True


class ApplicationDispatcher(LocalControlDispatcher):
    def __init__(self, registry, execute_tool, service):
        super().__init__(registry, execute_tool, catalog={'local_control_contract': {'version': 1, 'commands': []}})
        self._normalizer = ApplicationNormalizer(service)
        self.service = service

    def _eligible(self, tool_name):
        return self.service.enabled and tool_name in TOOLS.values()

    def dispatch(self, text: str, *, interaction_id: str | None = None):
        correlation = interaction_id or self.new_interaction_id()
        with self._lock:
            # Never evict a used ID and then execute its replay. Restart is explicit.
            if correlation not in self._history and len(self._history) >= self._max_history:
                return LocalControlDecision(True, False, 'interaction_limit',
                    'Limite de interações do catálogo atingido; reinicie o runtime.', interaction_id=correlation)
            return super().dispatch(text, interaction_id=correlation)
