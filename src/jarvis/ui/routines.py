"""Literal routine editing; execution uses the shared conversation channel."""
from copy import deepcopy
from uuid import uuid4

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QListWidget, QPushButton

from jarvis.ui.administration import AdministrativePage


class RoutinesPage(AdministrativePage):
    def __init__(self, client, submit, cancel, parent=None):
        super().__init__(client, parent)
        self.submit, self.cancel = submit, cancel
        self.data = {}
        self.definition = None
        self.list = QListWidget()
        self.name = QLineEdit()
        self.name.setPlaceholderText('Nome da rotina')
        self.command = QLineEdit()
        self.command.setPlaceholderText('Comando exclusivo: rotina nome')
        self.enabled = QCheckBox('Habilitar esta definição explicitamente')
        self.steps = QListWidget()
        self.choice = QComboBox()
        for widget in (self.list, self.name, self.command, self.enabled, self.steps, self.choice):
            self.layout.addWidget(widget)
        self.list.currentRowChanged.connect(self.select)
        actions = QHBoxLayout()
        for title, function in [('Criar', self.create), ('Adicionar etapa', self.add),
                                ('Remover etapa', self.remove), ('Subir', lambda: self.move(-1)),
                                ('Descer', lambda: self.move(1)), ('Salvar', self.save),
                                ('Excluir definição', self.delete), ('Prévia', self.preview),
                                ('Executar prévia', self.execute), ('Cancelar', self.cancel),
                                ('Resultado', self.refresh)]:
            button = QPushButton(title)
            button.clicked.connect(function)
            actions.addWidget(button)
        self.layout.addLayout(actions)
        self.token = None
        self.name.textChanged.connect(self.invalidate)
        self.command.textChanged.connect(self.invalidate)
        self.enabled.toggled.connect(self.invalidate)

    def invalidate(self, *_args):
        self.token = None

    def refresh(self):
        def loaded(data):
            self.data = data
            self.list.clear()
            for row in data['rows']:
                self.list.addItem(row['name'])
            self.choice.clear()
            for option in data['choices']:
                self.choice.addItem(option['label'], option)
            self.status.setText('Escolha fontes/alvos; exemplos não executam. '
                                'Excluir definição não desfaz efeitos. Confirmações na Conversa.\n' +
                                '\n'.join(self.history_text(r) for r in data['history'][-5:]))
        self.request('routines.list', {}, loaded)

    def select(self, index):
        if not self.data or index < 0 or index >= len(self.data['rows']):
            return
        self.definition = deepcopy(self.data['rows'][index])
        self.name.setText(self.definition['name'])
        self.command.setText(self.definition['command'])
        self.enabled.setChecked(self.definition['enabled'])
        self.draw_steps()

    def create(self):
        self.definition = {'id': uuid4().hex, 'name': 'Minha composição',
                           'command': 'rotina minha composicao', 'enabled': False, 'steps': []}
        self.name.setText(self.definition['name'])
        self.command.setText(self.definition['command'])
        self.enabled.setChecked(False)
        self.draw_steps()

    def draw_steps(self):
        self.token = None
        self.steps.clear()
        for step in self.definition['steps']:
            option = next((o for o in self.data.get('choices', []) if
                           all(o[k] == step[k] for k in ('kind', 'resource', 'identity'))), None)
            self.steps.addItem(option['label'] if option else
                               'Etapa indisponível — confira fonte e alvo antes de preparar a prévia.')

    def history_text(self, run):
        labels = {'running': 'em andamento', 'confirmation': 'aguardando confirmação',
                  'executing': 'executando', 'interrupted': 'interrompida', 'cancelled': 'cancelada',
                  'success': 'concluída', 'pending': 'pendente', 'error': 'falhou', 'denied': 'negada',
                  'security_blocked': 'bloqueada por segurança', 'not_found': 'alvo indisponível',
                  'confirmation_required': 'confirmação necessária'}
        name = next((r['name'] for r in self.data.get('rows', []) if r['id'] == run['routine']), 'Rotina removida')
        return f"{name}: {labels.get(run['status'], 'indisponível')}; etapas " + ', '.join(
            labels.get(s['status'], 'indisponível') for s in run['steps'])

    def add(self):
        option = self.choice.currentData()
        if self.definition is not None and option:
            self.definition['steps'].append({k: option[k] for k in ('kind', 'resource', 'identity')})
            self.draw_steps()

    def remove(self):
        index = self.steps.currentRow()
        if self.definition is not None and index >= 0:
            self.definition['steps'].pop(index)
            self.draw_steps()

    def move(self, offset):
        index = self.steps.currentRow()
        if self.definition is not None and 0 <= index + offset < len(self.definition['steps']) and index >= 0:
            stages = self.definition['steps']
            stages[index], stages[index + offset] = stages[index + offset], stages[index]
            self.draw_steps()
            self.steps.setCurrentRow(index + offset)

    def save(self):
        if self.definition is None or not self.data:
            return
        row = deepcopy(self.definition)
        row.update(name=self.name.text().strip(), command=self.command.text().strip(), enabled=self.enabled.isChecked())
        self.token = None
        self.request('routines.save', {'definition': row, 'revision': self.data['revision']}, lambda _data: self.refresh())

    def delete(self):
        if self.definition is None or not self.data:
            return
        self.token = None
        self.request('routines.delete', {'id': self.definition['id'], 'revision': self.data['revision']}, lambda _data: self.refresh())

    def preview(self):
        if self.definition is None:
            return
        def shown(data):
            self.token = data['token'] if data['ready'] else None
            self.status.setText(data['text'] + '\nPrévia da definição SALVA; salve alterações antes de preparar.')
        self.request('routines.preview', {'id': self.definition['id']}, shown)

    def execute(self):
        if self.token:
            token, self.token = self.token, None
            self.submit('/rotina executar ' + token)
        else:
            self.status.setText('Salve e prepare uma prévia disponível antes de executar.')
