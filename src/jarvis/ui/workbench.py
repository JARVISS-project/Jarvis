"""Personal workbench: navigation search, local capture and display settings."""
from datetime import datetime
import unicodedata

from PySide6.QtCore import Qt, QDateTime, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from jarvis.application.personal import SHORTCUTS, DesktopPreferences
from jarvis.ui.administration import AdministrativePage, literal_label


class InlineSurface(QFrame):
    def __init__(self, page, restore, parent=None):
        super().__init__(parent)
        self.setObjectName('detailPanel')
        self.page, self.restore = page, restore
        self.closed = False
        self.cleanup = QTimer(self)
        self.cleanup.setInterval(50)
        self.cleanup.timeout.connect(self._dispose)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        layout.addWidget(scroll)
        close = QPushButton('Voltar')
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    def reject(self):
        if self.closed:
            return
        self.closed = True
        cancel = getattr(self.page, 'cancel', None)
        if callable(cancel):
            cancel()
        self.hide()
        self.restore()
        self.cleanup.start()

    def _dispose(self):
        # Administrative callbacks must finish before deleting their widgets.
        if not getattr(self.page, 'busy', False):
            self.cleanup.stop()
            parent = self.parentWidget()
            if hasattr(parent, 'removeWidget'):
                parent.removeWidget(self)
            self.deleteLater()


SHORTCUT_LABELS = {'commands': 'Buscar ações', 'home': 'Hoje', 'calendar': 'Agenda',
    'conversation': 'Conversa', 'contacts': 'Contatos', 'automations': 'Automações',
    'find': 'Buscar nesta área', 'new_event': 'Novo compromisso (rascunho)', 'quick_task': 'Captura rápida'}


def title(text):
    label = literal_label(text)
    label.setObjectName('pageTitle')
    return label


class CommandPalette(QDialog):
    def __init__(self, client, actions, parent=None):
        super().__init__(parent)
        self.setWindowTitle('O que você quer fazer?')
        self.resize(640, 430)
        self.actions = list(actions)
        self.contacts = []
        self.closed = False
        layout = QVBoxLayout(self)
        layout.addWidget(title('O que você quer fazer?'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar área, ação ou contato…')
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('Buscar ações e contatos')
        layout.addWidget(self.search)
        self.rows = QListWidget()
        self.rows.itemActivated.connect(self.activate)
        layout.addWidget(self.rows, 1)
        layout.addWidget(literal_label('Enter abre a ação selecionada · Esc fecha · Nenhum envio é aprovado por atalho.'))
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(lambda: self.activate(self.rows.currentItem()))
        self.filter()
        self.finished.connect(lambda _result: setattr(self, 'closed', True))
        client.submit('contacts.list', {}, self.contacts_loaded)

    def contacts_loaded(self, event):
        if not self.closed and event.get('status') == 'done':
            self.contacts = event['data']['rows']
            self.filter()

    def filter(self):
        def normalized(value):
            return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
        query = normalized(self.search.text()).strip()
        self.rows.clear()
        for label, callback, shortcut in self.actions:
            if query in normalized(label):
                item = QListWidgetItem(label + ('  ·  ' + shortcut if shortcut else ''))
                item.setData(Qt.ItemDataRole.UserRole, callback)
                self.rows.addItem(item)
        for contact in self.contacts:
            if query and query in normalized(' '.join((contact['name'], contact['email'], contact['whatsapp']))):
                handler = next((callback for label, callback, _key in self.actions if label == 'Contatos'), None)
                if handler:
                    item = QListWidgetItem('Contato: ' + contact['name'] + ' · ' + (contact['email'] or contact['whatsapp']))
                    item.setData(Qt.ItemDataRole.UserRole, lambda contact=contact, handler=handler: handler(contact['id']))
                    self.rows.addItem(item)
        if self.rows.count():
            self.rows.setCurrentRow(0)

    def activate(self, item):
        if item is not None:
            callback = item.data(Qt.ItemDataRole.UserRole)
            self.accept()
            callback()


class PersonalPanel(AdministrativePage):
    def __init__(self, client, parent=None):
        super().__init__(client, parent)
        self.data = {'rows': [], 'revision': ''}
        self.dialog = None
        self.undo = None
        self.setObjectName('personalPanel')
        self.layout.setContentsMargins(14, 14, 14, 14)
        heading = title('Minhas prioridades')
        heading.setObjectName('sectionTitle')
        self.layout.insertWidget(0, heading)
        self.add_button = QPushButton('Tarefa ou lembrete')
        self.add_button.clicked.connect(self.capture)
        self.layout.addWidget(self.add_button)
        self.rows = QListWidget()
        self.rows.setMinimumHeight(105)
        self.rows.setMaximumHeight(200)
        self.rows.itemDoubleClicked.connect(lambda _item: self.capture(edit=True))
        self.layout.addWidget(self.rows)
        actions = QHBoxLayout()
        self.done_button, self.edit_button, self.delete_button = QPushButton('Concluir'), QPushButton('Editar'), QPushButton('Remover')
        self.done_button.clicked.connect(self.complete)
        self.edit_button.clicked.connect(lambda: self.capture(edit=True))
        self.delete_button.clicked.connect(lambda: self.change('delete'))
        for button in (self.done_button, self.edit_button, self.delete_button):
            actions.addWidget(button)
        self.layout.addLayout(actions)
        self.show_done = QCheckBox('Mostrar concluídas')
        self.show_done.toggled.connect(lambda _checked: self.render())
        self.layout.addWidget(self.show_done)
        self.undo_button = QPushButton('Desfazer última ação')
        self.undo_button.clicked.connect(self.restore)
        self.undo_button.hide()
        self.layout.addWidget(self.undo_button)
        self.controls = [self.rows, self.add_button, self.done_button, self.edit_button, self.delete_button, self.undo_button]
        self.rows.currentItemChanged.connect(lambda *_args: self.after_request())
        self.layout.addStretch(1)

    def refresh(self):
        self.request('personal.list', {'query': ''}, self.loaded)

    def loaded(self, data):
        self.data = data
        self.render()

    def render(self):
        self.rows.clear()
        for row in self.data['rows']:
            if row['done'] and not self.show_done.isChecked():
                continue
            due = datetime.fromisoformat(row['due']).astimezone() if row['due'] else None
            suffix = (' · ' + due.strftime('%d/%m %H:%M')) if due else ''
            overdue = due is not None and due < datetime.now().astimezone() and not row['done']
            prefix = 'Concluída · ' if row['done'] else 'Atrasado · ' if overdue else 'Lembrete · ' if row['kind'] == 'reminder' else ''
            item = QListWidgetItem(prefix + row['title'] + suffix)
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.rows.addItem(item)
        active = sum(not row['done'] for row in self.data['rows'])
        self.status.setText(f'{active} pendência(s) pessoal(is).' if active else 'Capture o que você não quer esquecer.')
        self.after_request()

    def selected(self):
        item = self.rows.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def complete(self):
        row = self.selected()
        if row:
            self.change('reopen' if row['done'] else 'done')

    def change(self, action):
        row = self.selected()
        if row:
            inverse = {'delete': 'restore', 'done': 'reopen', 'reopen': 'done'}[action]
            def changed(data):
                self.undo = (row['id'], inverse)
                self.undo_button.show()
                self.loaded(data)
            self.request('personal.change', {'id': row['id'], 'action': action, 'revision': self.data['revision']}, changed)

    def restore(self):
        if self.undo:
            id, action = self.undo
            def done(data):
                self.undo = None
                self.undo_button.hide()
                self.loaded(data)
            self.request('personal.change', {'id': id, 'action': action, 'revision': self.data['revision']}, done)

    def capture(self, _checked=False, *, edit=False, reminder=False):
        if self.busy:
            return
        if not self.data['revision']:
            self.request('personal.list', {'query': ''}, lambda data: (self.loaded(data), self.capture(edit=edit, reminder=reminder)))
            return
        old = self.selected() if edit else None
        if edit and old is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Editar item pessoal' if old else 'Captura rápida')
        dialog.resize(510, 330)
        layout = QVBoxLayout(dialog)
        layout.addWidget(title('Tire da cabeça. Guarde aqui.'))
        text = QLineEdit(old['title'] if old else '')
        text.setMaxLength(200)
        text.setPlaceholderText('O que você precisa fazer ou lembrar?')
        layout.addWidget(text)
        kind = QComboBox()
        kind.addItem('Tarefa', 'task')
        kind.addItem('Lembrete', 'reminder')
        kind.setCurrentIndex(kind.findData(old['kind'] if old else 'reminder' if reminder else 'task'))
        scheduled = QCheckBox('Definir data e horário')
        scheduled.setChecked(bool(old and old['due']) or reminder)
        due = QDateTimeEdit()
        due.setCalendarPopup(True)
        due.setDateTime(QDateTime.fromString(old['due'], Qt.DateFormat.ISODate).toLocalTime() if old and old['due'] else QDateTime.currentDateTime().addSecs(3600))
        due.setEnabled(scheduled.isChecked())
        scheduled.toggled.connect(due.setEnabled)
        def type_changed(_index):
            if kind.currentData() == 'reminder':
                scheduled.setChecked(True)
        kind.currentIndexChanged.connect(type_changed)
        priority = QComboBox()
        priority.addItem('Prioridade normal', 'normal')
        priority.addItem('Importante', 'high')
        priority.setCurrentIndex(priority.findData(old['priority'] if old else 'normal'))
        form = QFormLayout()
        form.addRow('Tipo', kind)
        form.addRow('', scheduled)
        form.addRow('Horário do seu computador', due)
        form.addRow('Prioridade', priority)
        layout.addLayout(form)
        layout.addWidget(literal_label('Fica neste perfil. Não cria evento na agenda nem envia mensagens. Lembretes aparecem no painel enquanto o JARVIS estiver aberto.'))
        error = literal_label('')
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Salvar')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Cancelar')
        layout.addWidget(buttons)
        def save():
            if not text.text().strip():
                error.setText('Escreva o que você quer guardar.')
                return
            if kind.currentData() == 'reminder' and not scheduled.isChecked():
                error.setText('Escolha uma data para o lembrete.')
                return
            values = {'id': old['id'] if old else None, 'title': text.text(), 'kind': kind.currentData(),
                'due': due.dateTime().toUTC().toString(Qt.DateFormat.ISODate) if scheduled.isChecked() else None,
                'priority': priority.currentData(), 'revision': self.data['revision']}
            buttons.setEnabled(False)
            def result(event):
                if event.get('status') == 'done':
                    self.loaded(event['data'])
                    dialog.accept()
                else:
                    error.setText(event.get('error', 'Não foi possível salvar.'))
                    buttons.setEnabled(True)
            self.client.submit('personal.save', values, result)
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        dialog.title_edit, dialog.kind, dialog.scheduled, dialog.due = text, kind, scheduled, due
        self.dialog = dialog
        dialog.open()
        text.setFocus()

    def after_request(self):
        if hasattr(self, 'done_button'):
            enabled = bool(self.selected()) and not self.busy
            for button in (self.done_button, self.edit_button, self.delete_button):
                button.setEnabled(enabled)
            self.undo_button.setEnabled(bool(self.undo) and not self.busy)


class SettingsHub(AdministrativePage):
    def __init__(self, client, navigate, on_preferences, parent=None):
        super().__init__(client, parent)
        self.data = {}
        self.on_preferences = on_preferences
        self.layout.setContentsMargins(28, 24, 28, 24)
        self.layout.addWidget(title('Deixe o JARVIS do seu jeito'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        column = QVBoxLayout(content)
        column.setSpacing(15)
        self.theme, self.density = QComboBox(), QComboBox()
        for label, value in [('Petróleo e menta · escuro', 'dark'), ('Claro e verde', 'light'), ('Acompanhar o sistema', 'system')]:
            self.theme.addItem(label, value)
        self.density.addItem('Confortável', 'comfortable')
        self.density.addItem('Compacta', 'compact')
        form = QFormLayout()
        form.addRow('Aparência', self.theme)
        form.addRow('Densidade', self.density)
        column.addLayout(form)
        column.addWidget(literal_label('Atalhos funcionam dentro do JARVIS. Esc fecha revisões; nenhum atalho aprova um envio.'))
        self.keys = {}
        keys_form = QFormLayout()
        for key, label in SHORTCUT_LABELS.items():
            edit = QKeySequenceEdit(QKeySequence(SHORTCUTS[key]))
            self.keys[key] = edit
            keys_form.addRow(label, edit)
        column.addLayout(keys_form)
        save = QPushButton('Salvar aparência e atalhos')
        save.setObjectName('primaryAction')
        save.clicked.connect(self.save)
        column.addWidget(save)
        self.save_button = save
        column.addWidget(title('Recursos e conexões'))
        grid = QGridLayout()
        destinations = [('Conversa e voz', 'Ajustes'), ('Contas e conexões', 'Contas e conexões'),
            ('Permissões', 'Permissões'), ('Memória pessoal', 'Memória'), ('Aplicativos', 'Aplicativos'),
            ('Organizar arquivos', 'Organização de arquivos'), ('Rotinas', 'Rotinas'), ('Ajuda e primeiros passos', 'Ajuda')]
        for index, (label, destination) in enumerate(destinations):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, page=destination: navigate(page))
            grid.addWidget(button, index // 2, index % 2)
        column.addLayout(grid)
        column.addStretch(1)
        scroll.setWidget(content)
        self.layout.addWidget(scroll, 1)
        self.controls = [self.theme, self.density, save, *self.keys.values()]

    def refresh(self):
        self.request('ui.preferences', {}, self.loaded)

    def loaded(self, data):
        self.data = data
        value = data['preferences']
        self.theme.setCurrentIndex(self.theme.findData(value['theme']))
        self.density.setCurrentIndex(self.density.findData(value['density']))
        for key, widget in self.keys.items():
            widget.setKeySequence(QKeySequence(value['shortcuts'][key]))
        self.status.setText('Aparência e atalhos pertencem a este perfil.')

    def save(self):
        if not self.data:
            return
        value = {**self.data['preferences'], 'theme': self.theme.currentData(), 'density': self.density.currentData(),
                 'shortcuts': {key: widget.keySequence().toString(QKeySequence.SequenceFormat.PortableText) for key, widget in self.keys.items()}}
        try:
            DesktopPreferences.validate(value)
        except ValueError as error:
            self.status.setText(str(error))
            return
        def saved(data):
            self.loaded(data)
            self.on_preferences(data)
        self.request('ui.save', {'preferences': value, 'revision': self.data['revision']}, saved)
