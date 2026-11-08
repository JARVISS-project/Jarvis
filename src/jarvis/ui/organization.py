"""Literal local organization UI; file effects use existing text/executor."""
from __future__ import annotations

import json

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton

from jarvis.organization.service import CATEGORIES, default_rules
from jarvis.ui.administration import AdministrativePage, literal_label


class OrganizationPage(AdministrativePage):
    def __init__(self, client, submit_command, parent=None, *, cancel_command=None):
        super().__init__(client, parent)
        self.submit_command = submit_command
        self.cancel_command = cancel_command
        self.data, self.plan, self.pending = {}, None, None
        self.folder = literal_label('Escolha uma pasta; Downloads é sugestão, não autorização.')
        self.layout.addWidget(self.folder)
        self.choose_button = QPushButton('Escolher pasta…')
        self.choose_button.clicked.connect(self.choose_folder)
        self.layout.addWidget(self.choose_button)
        self.extensions = {}
        for category, defaults in CATEGORIES.items():
            row = QHBoxLayout()
            row.addWidget(literal_label(category))
            edit = QLineEdit(', '.join(defaults))
            self.extensions[category] = edit
            row.addWidget(edit)
            self.layout.addLayout(row)
        self.date = QCheckBox('Agrupar por mês da última modificação (UTC, filesystem)')
        self.recursive = QCheckBox('Incluir arquivos de subpastas (até 5 níveis; não mover pastas)')
        self.template = QLineEdit()
        self.template.setPlaceholderText('Opcional: {name}_{date}_{n}. Vazio preserva nome; extensão preservada.')
        self.collision = QComboBox()
        self.collision.addItem('Conflito: manter para revisão', 'review')
        self.collision.addItem('Conflito: sufixo determinístico (1), (2)…', 'suffix')
        for control in (self.date, self.recursive, self.template, self.collision):
            self.layout.addWidget(control)
        self.rules_button = QPushButton('Salvar regras (invalida prévia anterior)')
        self.rules_button.clicked.connect(self.save_rules)
        self.analyze_button = QPushButton('Analisar e mostrar plano — sem mover')
        self.analyze_button.clicked.connect(self.analyze)
        self.layout.addWidget(self.rules_button)
        self.layout.addWidget(self.analyze_button)
        grant = QHBoxLayout()
        self.action = QComboBox()
        self.action.addItem('Organizar esta pasta com estas regras', 'organization_execute')
        self.action.addItem('Desfazer movimentos comprovados deste escopo', 'organization_undo')
        self.mode = QComboBox()
        for text, value in [('Perguntar', 'ask'), ('Bloquear', 'block'), ('Permitir neste escopo', 'allow')]:
            self.mode.addItem(text, value)
        self.grant_button = QPushButton('Aplicar permissão persistente')
        self.grant_button.clicked.connect(self.grant)
        for control in (self.action, self.mode, self.grant_button):
            grant.addWidget(control)
        self.layout.addLayout(grant)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.layout.addWidget(self.preview)
        self.history = QComboBox()
        self.history.activated.connect(self.select_history)
        self.layout.addWidget(self.history)
        self.list_history_button = QPushButton('Histórico local desta pasta')
        self.list_history_button.clicked.connect(self.list_history)
        self.layout.addWidget(self.list_history_button)
        effects = QHBoxLayout()
        self.execute_button = QPushButton('Executar plano pelo executor')
        self.undo_button = QPushButton('Desfazer plano selecionado')
        self.cancel_button = QPushButton('Cancelar antes da próxima operação')
        self.history_button = QPushButton('Recarregar resultado / reconciliar')
        self.execute_button.clicked.connect(lambda: self.operate('execute'))
        self.undo_button.clicked.connect(lambda: self.operate('undo'))
        self.cancel_button.clicked.connect(self.cancel)
        self.history_button.clicked.connect(self.reload_plan)
        for control in (self.execute_button, self.undo_button, self.cancel_button, self.history_button):
            effects.addWidget(control)
        self.layout.addLayout(effects)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar nome/localização no índice autorizado, sem conteúdo')
        self.search.returnPressed.connect(self.search_index)
        self.layout.addWidget(self.search)
        self.index = QPlainTextEdit()
        self.index.setReadOnly(True)
        self.layout.addWidget(self.index)
        self.controls = [self.choose_button, self.rules_button, self.analyze_button, self.grant_button,
            self.execute_button, self.undo_button, self.history_button, self.list_history_button, self.history]
        self.after_request()

    def after_request(self):
        selected = bool(self.data.get('selected'))
        for control in self.controls:
            control.setEnabled(not self.busy and not self.pending and (control is self.choose_button or selected))
        self.execute_button.setEnabled(not self.busy and not self.pending and bool(self.plan and not self.plan['executed']))
        self.undo_button.setEnabled(not self.busy and not self.pending and bool(self.plan and self.plan['executed']))
        self.history_button.setEnabled(not self.busy and not self.pending and self.plan is not None)
        self.cancel_button.setEnabled(self.pending is not None)

    def refresh(self):
        if not self.pending:
            self.request('organization.state', {}, self.loaded)

    def loaded(self, data):
        self.data = data
        self.folder.setText(data.get('folder', data['explanation']))
        rules = data['rules']
        for category, edit in self.extensions.items():
            edit.setText(', '.join(rules['extensions'][category]))
        self.date.setChecked(rules['date_group'])
        self.recursive.setChecked(rules['recursive'])
        self.template.setText(rules['rename'])
        self.collision.setCurrentIndex(max(0, self.collision.findData(rules['collision'])))
        self.status.setText(data['explanation'] + ('\nPolítica efetiva: ' + data['policy'] if 'policy' in data else ''))
        self.after_request()

    def choose_folder(self):
        suggestion = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        path = QFileDialog.getExistingDirectory(self, 'Escolher raiz explícita de organização', suggestion)
        if path:
            self.plan = None
            self.preview.clear()
            self.index.clear()
            self.request('organization.select', {'path': path}, self.loaded)

    def save_rules(self):
        if not self.data.get('selected'):
            return
        rules = {**default_rules(), 'extensions': {category: [x.strip().lower() for x in edit.text().split(',') if x.strip()] for category, edit in self.extensions.items()},
            'date_group': self.date.isChecked(), 'recursive': self.recursive.isChecked(), 'rename': self.template.text(), 'collision': self.collision.currentData()}
        self.plan = None
        self.preview.clear()
        self.request('organization.rules', {'root': self.data['selected'], 'rules': rules}, self.loaded)

    def grant(self):
        if self.data.get('selected'):
            self.plan = None
            self.preview.clear()
            self.request('organization.grant', {'root': self.data['selected'], 'action': self.action.currentData(), 'mode': self.mode.currentData(), 'revision': self.data['revision']}, self.loaded)

    def analyze(self):
        if self.data.get('selected'):
            self.request('organization.analyze', {'root': self.data['selected']}, self.show_plan)

    def show_plan(self, plan):
        self.plan = plan
        lines = [f"Plano v{plan['schema']} · {plan['id']} · {len(plan['operations'])} operações · lote não atômico"]
        for row in plan['operations']:
            lines.append(f"{row['source']} → {row['destination']} | {row['reason']} | renomear={row['renamed']} | {row['state']} / desfazer={row['undo']} | {row['error']}")
        lines.extend(f"IGNORADO: {row['source']} | {row['reason']}" for row in plan['ignored'])
        self.preview.setPlainText('\n'.join(lines))
        self.status.setText('Prévia/resultado local. Revise origem, destino, regras e conflitos. Sem exclusão ou envio externo.')
        self.after_request()

    def reload_plan(self):
        if self.plan:
            self.request('organization.plan', {'plan': self.plan['id']}, self.show_plan)

    def list_history(self):
        if self.data.get('selected'):
            self.request('organization.history', {'root': self.data['selected']}, self.loaded_history)

    def loaded_history(self, data):
        self.history.clear()
        self.history.addItem('Selecione um plano; não retoma movimentos automaticamente', None)
        for row in data['rows']:
            self.history.addItem(f"{row['created']} | {row['operations']} operações | executado={row['executed']}", row['id'])

    def select_history(self, _index):
        plan_id = self.history.currentData()
        if plan_id:
            self.request('organization.plan', {'plan': plan_id}, self.show_plan)

    def search_index(self):
        if self.data.get('selected'):
            self.request('organization.index', {'root': self.data['selected'], 'query': self.search.text()}, lambda data: self.index.setPlainText('\n'.join(f"{row['origin']} → {row['current']} | {row['category']} | {row['date']} | {row['operation']}" for row in data['rows']) or 'Índice vazio neste escopo.'))

    def operate(self, action):
        if not self.plan or self.pending or self.busy:
            return
        result = self.submit_command(f"/organization {action} {self.plan['id']}")
        if result:
            self.pending = result
            self.after_request()

    def cancel(self):
        if self.pending and callable(self.cancel_command):
            self.cancel_command()
        else:
            self.request('organization.cancel', {}, lambda data: self.status.setText(data['message']))

    def handle_text_event(self, event):
        if event.get('status') in {'ready', 'disconnected'}:
            self.pending = None
            self.plan = None
            self.preview.clear()
            self.history.clear()
            self.after_request()
        elif self.pending == (event.get('generation'), event.get('interaction')) and event.get('status') in {'done', 'error', 'cancelled'}:
            self.pending = None
            self.status.setText(event.get('text', event['status']))
            self.after_request()
            self.reload_plan()
