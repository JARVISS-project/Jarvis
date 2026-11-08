"""Local catalog administration; effects use the existing conversation executor."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox

from jarvis.ui.administration import AdministrativePage, literal_label


class ApplicationsPage(AdministrativePage):
    def __init__(self, client, submit_command, parent=None):
        super().__init__(client, parent)
        self.submit_command = submit_command
        self.data = {'rows': [], 'monitors': [], 'revision': ''}
        self.pending = None
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar aplicativo descoberto')
        self.search.textChanged.connect(self.filter_rows)
        self.refresh_button = QPushButton('Atualizar catálogo')
        self.refresh_button.clicked.connect(self.refresh)
        bar = QHBoxLayout()
        bar.addWidget(self.search)
        bar.addWidget(self.refresh_button)
        self.layout.addLayout(bar)
        self.rows = QListWidget()
        self.rows.currentItemChanged.connect(self.show_selected)
        self.layout.addWidget(self.rows)
        self.details = literal_label('Selecione um aplicativo. Descoberta não autoriza acesso.')
        self.layout.addWidget(self.details)
        grants = QHBoxLayout()
        self.group = QComboBox()
        for label, group in [('Abrir', 'open'), ('Consultar', 'status'), ('Controlar janela', 'window'), ('Fechar', 'close')]:
            self.group.addItem(label, group)
        self.mode = QComboBox()
        for label, mode in [('Bloquear', 'block'), ('Perguntar', 'ask'), ('Permitir este aplicativo', 'allow')]:
            self.mode.addItem(label, mode)
        self.apply_button = QPushButton('Aplicar permissão')
        self.remove_button = QPushButton('Remover autorização')
        self.apply_button.clicked.connect(lambda: self.change_permission(False))
        self.remove_button.clicked.connect(lambda: self.change_permission(True))
        for widget in (self.group, self.mode, self.apply_button, self.remove_button):
            grants.addWidget(widget)
        self.layout.addLayout(grants)
        self.windows = QComboBox()
        self.clear_windows()
        self.layout.addWidget(self.windows)
        effects = QHBoxLayout()
        self.effect_buttons = {}
        for label, action in [('Abrir', 'open'), ('Consultar janelas', 'status'), ('Focar', 'focus'), ('Minimizar', 'minimize'), ('Maximizar', 'maximize'), ('Restaurar', 'restore'), ('Fechar', 'close')]:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, action=action: self.operate(action))
            self.effect_buttons[action] = button
            effects.addWidget(button)
        self.layout.addLayout(effects)
        preferences = QHBoxLayout()
        self.monitor = QComboBox()
        self.monitor.addItem('Monitor principal disponível', '')
        preferences.addWidget(self.monitor)
        self.geometry = {}
        for key, label in [('x', 'X %'), ('y', 'Y %'), ('width', 'Largura %'), ('height', 'Altura %')]:
            spin = QSpinBox()
            spin.setRange(0 if key in {'x', 'y'} else 10, 100)
            spin.setValue(0 if key in {'x', 'y'} else 80)
            spin.setPrefix(label + ' ')
            self.geometry[key] = spin
            preferences.addWidget(spin)
        self.layout.addLayout(preferences)
        self.aliases = QLineEdit()
        self.aliases.setPlaceholderText('Aliases explícitos separados por vírgula; colisões são rejeitadas')
        self.layout.addWidget(self.aliases)
        self.save_button = QPushButton('Salvar preferências e aliases')
        self.place_button = QPushButton('Aplicar posição/tamanho pela conversa')
        self.save_button.clicked.connect(self.save_preferences)
        self.place_button.clicked.connect(lambda: self.operate('place'))
        self.layout.addWidget(self.save_button)
        self.layout.addWidget(self.place_button)
        self.layout.addWidget(literal_label('Maximizar é controle de janela; tela cheia interna não é implementada. Fechar exige confirmação na conversa. Revogar não desfaz efeitos anteriores.'))
        self.controls = [self.refresh_button, self.apply_button, self.remove_button, self.save_button, self.place_button, *self.effect_buttons.values()]
        self.show_selected()

    def selected(self):
        item = self.rows.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def after_request(self):
        row = self.selected()
        for control in self.controls:
            control.setEnabled(not self.pending and (control is self.refresh_button or bool(row and row['available'])))

    def refresh(self):
        if self.pending:
            return
        self.clear_windows()
        self.request('apps.refresh', {}, self.loaded)

    def loaded(self, data):
        self.data = data
        self.monitor.clear()
        self.monitor.addItem('Monitor principal disponível', '')
        for monitor in data['monitors']:
            self.monitor.addItem(monitor['id'], monitor['id'])
        self.filter_rows()
        self.status.setText(data['explanation'] + ('\n' + '\n'.join(data['diagnostics']) if data['diagnostics'] else ''))

    def filter_rows(self):
        self.rows.clear()
        query = self.search.text().casefold()
        for row in self.data['rows']:
            if query not in (row['name'] + ' ' + row['source'] + ' ' + row['kind']).casefold():
                continue
            item = QListWidgetItem(f"{row['name']} · {row['kind']} · {row['source']} · {'disponível' if row['available'] else 'indisponível'}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.rows.addItem(item)
        self.show_selected()

    def clear_windows(self):
        self.windows.clear()
        self.windows.addItem('Sem seleção — consulte janelas antes de escolher', None)

    def show_selected(self, *_):
        self.clear_windows()
        row = self.selected()
        self.details.setText('Selecione um aplicativo.' if row is None else f"{row['name']}\nID: {row['id']}\nIdentidade: {row['identity']}\nOrigem: {row['source']}\n{row['reason']}\n" + '\n'.join(f'{action}: {mode}' for action, mode in sorted(row['policies'].items())))
        available = bool(row and row['available']) and not self.busy and not self.pending
        for control in self.controls:
            control.setEnabled(available if control is not self.refresh_button else not self.busy and not self.pending)
        if row:
            preference = row['preference'] or {'monitor': '', 'x': 0, 'y': 0, 'width': 80, 'height': 80}
            self.monitor.setCurrentIndex(max(0, self.monitor.findData(preference['monitor'])))
            for key, spin in self.geometry.items():
                spin.setValue(preference[key])
            self.aliases.setText(', '.join(row.get('aliases', [])))

    def change_permission(self, remove):
        row = self.selected()
        if not row or self.pending:
            return
        self.request('apps.permission', {'application': row['id'], 'fingerprint': row['identity'], 'group': self.group.currentData(), 'mode': self.mode.currentData(), 'revision': self.data['revision'], 'remove': remove}, self.loaded)

    def save_preferences(self):
        row = self.selected()
        if not row or self.pending:
            return
        self.request('apps.preferences', {'application': row['id'], 'fingerprint': row['identity'], 'preference': {'monitor': self.monitor.currentData(), **{key: spin.value() for key, spin in self.geometry.items()}}, 'aliases': [value.strip() for value in self.aliases.text().split(',') if value.strip()]}, self.loaded)

    def operate(self, action):
        row = self.selected()
        if not row or self.busy or self.pending:
            return
        reference = self.windows.currentData()
        result = self.submit_command(f"/app {action} {row['id']}" + (f' {reference}' if reference else ''))
        if result:
            self.pending = (*result, action, row['id'], row['identity'])
            for button in self.controls:
                button.setEnabled(False)

    def handle_text_event(self, event):
        if event.get('status') in {'ready', 'disconnected'}:
            self.pending = None
            self.clear_windows()
            self.show_selected()
            return
        if not self.pending or (event.get('generation'), event.get('interaction')) != self.pending[:2]:
            return
        if event.get('status') not in {'done', 'error', 'cancelled'}:
            return
        pending, self.pending = self.pending, None
        row = self.selected()
        self.show_selected()
        self.status.setText(event.get('text', event['status']))
        if event['status'] == 'done' and pending[2] == 'status' and row and (row['id'], row['identity']) == pending[3:]:
            try:
                result = json.loads(event.get('text', ''))
                for window in result['windows']:
                    self.windows.addItem(f"PID {window['pid']} / HWND {window['hwnd']} · {window['title']}", window['reference'])
            except (ValueError, KeyError, TypeError):
                self.clear_windows()
                self.status.setText('Resposta de janelas inválida; nenhuma referência foi selecionada.')
