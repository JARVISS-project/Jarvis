"""Calendar advice, explicit contact linking and message review; never sends."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout,
)

from jarvis.application.contacts import ContactService
from jarvis.ui.administration import AdministrativePage, literal_label


class ContactsPage(AdministrativePage):
    def __init__(self, client, parent=None):
        super().__init__(client, parent)
        self.data = {'rows': [], 'revision': ''}
        self.selected = None
        self.layout.addWidget(literal_label('Seus contatos ficam neste perfil, protegidos pelo Windows. '
            'WhatsApp tem prioridade; o e-mail é a alternativa. Nenhuma agenda de contatos é importada automaticamente.'))
        self.rows = QListWidget()
        self.rows.currentItemChanged.connect(self.select)
        self.layout.addWidget(self.rows, 1)
        form = QFormLayout()
        self.name, self.email, self.phone = QLineEdit(), QLineEdit(), QLineEdit()
        self.name.setMaxLength(120)
        self.email.setMaxLength(254)
        self.phone.setMaxLength(30)
        self.name.setPlaceholderText('Nome do contato')
        self.email.setPlaceholderText('Opcional, se houver WhatsApp')
        self.phone.setPlaceholderText('+55 11 99999-9999')
        for label, widget in (('Nome', self.name), ('E-mail', self.email), ('WhatsApp com país', self.phone)):
            form.addRow(label, widget)
        self.layout.addLayout(form)
        actions = QHBoxLayout()
        self.new_button = QPushButton('Novo contato')
        self.save_button = QPushButton('Salvar contato')
        self.delete_button = QPushButton('Remover contato')
        self.new_button.clicked.connect(self.new)
        self.save_button.clicked.connect(self.save)
        self.delete_button.clicked.connect(self.delete)
        for button in (self.new_button, self.save_button, self.delete_button):
            actions.addWidget(button)
        self.layout.addLayout(actions)
        self.controls = [self.rows, self.name, self.email, self.phone, self.new_button, self.save_button, self.delete_button]
        self.confirmation = None
        self.requested_contact = None

    def refresh(self):
        self.request('contacts.list', {}, self.loaded)

    def loaded(self, data):
        self.data = data
        self.rows.clear()
        for row in data['rows']:
            channel = 'WhatsApp' if row['whatsapp'] else 'E-mail'
            item = QListWidgetItem(f"{row['name']} · {channel} · {row['email'] or row['whatsapp']}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.rows.addItem(item)
        self.new()
        if self.requested_contact:
            for index in range(self.rows.count()):
                if self.rows.item(index).data(Qt.ItemDataRole.UserRole)['id'] == self.requested_contact:
                    self.rows.setCurrentRow(index)
                    break
            self.requested_contact = None
        self.status.setText(f"{len(data['rows'])} contato(s) cadastrado(s) neste perfil.")

    def select(self, item, _previous=None):
        self.selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        for widget, key in ((self.name, 'name'), (self.email, 'email'), (self.phone, 'whatsapp')):
            widget.setText(self.selected[key] if self.selected else '')
        self.delete_button.setEnabled(bool(self.selected) and not self.busy)

    def new(self):
        self.rows.setCurrentRow(-1)
        self.select(None)

    def save(self):
        try:
            ContactService._validate(self.name.text(), self.email.text(), self.phone.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.request('contacts.save', {'id': self.selected['id'] if self.selected else None,
            'name': self.name.text(), 'email': self.email.text(), 'whatsapp': self.phone.text(),
            'revision': self.data['revision']}, self.loaded)

    def delete(self):
        if not self.selected or self.busy:
            return
        values = {'id': self.selected['id'], 'revision': self.data['revision']}
        dialog = QDialog(self)
        dialog.setWindowTitle('Remover contato local')
        layout = QVBoxLayout(dialog)
        layout.addWidget(literal_label('Remover ' + self.selected['name'] + ' deste perfil? A agenda do Google não será alterada.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText('Remover')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Cancelar')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.accepted.connect(lambda: self.request('contacts.delete', values, self.loaded))
        self.confirmation = dialog
        dialog.open()

    def after_request(self):
        self.delete_button.setEnabled(bool(self.selected) and not self.busy)


def contacts_dialog(client, parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle('Meus contatos')
    dialog.resize(600, 560)
    layout = QVBoxLayout(dialog)
    dialog.page = ContactsPage(client, dialog)
    layout.addWidget(dialog.page)
    close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    close.button(QDialogButtonBox.StandardButton.Close).setText('Fechar')
    close.rejected.connect(dialog.reject)
    layout.addWidget(close)
    dialog.page.refresh()
    return dialog


class CalendarAssistantPage(AdministrativePage):
    def __init__(self, client, alias, period, event, parent=None):
        super().__init__(client, parent)
        self.setObjectName('calendarAssistantContent')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet('''
            QWidget#calendarAssistantContent { background: #0b1823; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #293f50; }
        ''')
        self.arguments = {'alias': alias, 'period': period, 'fingerprint': event['fingerprint'], 'contact_key': ''}
        self.data = {}
        self.draft_data = None
        self.contacts_window = None
        title = literal_label(event['title'])
        title.setStyleSheet('font-size: 22px; font-weight: 600; color: #e5f4fa;')
        self.layout.addWidget(title)
        self.layout.addWidget(literal_label('Sugestões para se preparar, confirmar ou avisar alguém.'))
        selection = QVBoxLayout()
        self.people = QComboBox()
        self.people.setAccessibleName('Contato para este compromisso')
        self.people.currentIndexChanged.connect(self.person_changed)
        self.contacts_button = QPushButton('Meus contatos')
        self.contacts_button.clicked.connect(self.manage_contacts)
        selection.addWidget(self.people, 1)
        selection.addWidget(self.contacts_button)
        self.layout.addLayout(selection)
        self.contact_hint = literal_label('Nenhum contato selecionado.')
        self.layout.addWidget(self.contact_hint)
        self.ai_button = QPushButton('Contextualizar com IA local')
        self.ai_button.clicked.connect(lambda: self.refresh(use_ai=True))
        self.layout.addWidget(self.ai_button)
        self.suggestions = QListWidget()
        self.suggestions.setWordWrap(True)
        self.suggestions.setMinimumHeight(110)
        self.suggestions.setMaximumHeight(155)
        self.suggestions.currentItemChanged.connect(self.suggestion_changed)
        self.layout.addWidget(self.suggestions, 1)
        self.preparation = literal_label('')
        self.layout.addWidget(self.preparation)
        actions = QVBoxLayout()
        self.channel = QComboBox()
        self.channel.addItem('Automático: WhatsApp primeiro', 'auto')
        self.channel.addItem('WhatsApp', 'whatsapp')
        self.channel.addItem('E-mail', 'email')
        self.channel.currentIndexChanged.connect(self.clear_draft)
        self.draft_button = QPushButton('Preparar mensagem')
        self.draft_button.setObjectName('primaryAction')
        self.draft_button.clicked.connect(self.prepare_draft)
        actions.addWidget(self.channel, 1)
        actions.addWidget(self.draft_button)
        self.layout.addLayout(actions)
        self.draft_label = literal_label('O rascunho aparecerá aqui para você revisar.')
        self.layout.addWidget(self.draft_label)
        self.subject = QLineEdit()
        self.subject.setAccessibleName('Assunto do e-mail')
        self.subject.hide()
        self.layout.addWidget(self.subject)
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText('Nenhuma mensagem foi preparada ou enviada.')
        self.body.setMaximumHeight(115)
        self.body.setReadOnly(True)
        self.layout.addWidget(self.body)
        copies = QVBoxLayout()
        self.copy_button = QPushButton('Copiar mensagem revisada')
        self.copy_recipient = QPushButton('Copiar destinatário')
        self.copy_button.clicked.connect(self.copy_message)
        self.copy_recipient.clicked.connect(self.copy_destination)
        copies.addWidget(self.copy_button)
        copies.addWidget(self.copy_recipient)
        self.layout.addLayout(copies)
        self.notice = literal_label('Esta tela prepara rascunhos. O envio no WhatsApp ou no e-mail ainda não está conectado.')
        self.layout.addWidget(self.notice)
        self.controls = [self.people, self.contacts_button, self.ai_button, self.suggestions,
            self.channel, self.draft_button, self.copy_button, self.copy_recipient]
        self.after_request()

    def refresh(self, use_ai=False):
        if self.busy:
            return
        self.clear_draft()
        self.data = {}
        self.suggestions.clear()
        self.preparation.setText('')
        self.request('calendar.assist', {**self.arguments, 'use_ai': use_ai}, self.loaded)

    def loaded(self, data):
        self.data = data
        self.people.blockSignals(True)
        self.people.clear()
        self.people.addItem('Escolha um contato para vincular…', '')
        for person in data['people']:
            source = 'participante do evento' if person['in_event'] else 'contato local; vínculo manual'
            self.people.addItem(f"{person['name']} · {source}", person['key'])
        self.people.setCurrentIndex(max(0, self.people.findData(data['selected'])))
        self.people.blockSignals(False)
        self.arguments['contact_key'] = data['selected']
        person = next((row for row in data['people'] if row['key'] == data['selected']), None)
        self.contact_hint.setText(('WhatsApp preferido: ' + person['whatsapp'] if person['whatsapp'] else
            'E-mail disponível: ' + person['email'] if person['email'] else 'Contato sem canal disponível.') if person else
            'Selecione o contato certo. Pessoas com nomes iguais não são vinculadas automaticamente.')
        self.suggestions.clear()
        for suggestion in data['suggestions']:
            item = QListWidgetItem(suggestion['question'] + '\n' + suggestion['reason'])
            item.setData(Qt.ItemDataRole.UserRole, suggestion)
            self.suggestions.addItem(item)
        if self.suggestions.count():
            self.suggestions.setCurrentRow(0)
        self.preparation.setText('Para preparar:\n' + '\n'.join('• ' + item for item in data['preparation']))
        self.notice.setText('Nenhuma mensagem enviada. O envio nos aplicativos ainda não está conectado. '
            'Esta tela não altera a agenda.' + (' A lista de participantes pode estar incompleta.' if data['event'].get('participants_omitted') else ''))
        self.status.setText(data['mode'])

    def person_changed(self):
        self.arguments['contact_key'] = self.people.currentData() or ''
        self.refresh()

    def suggestion_changed(self, *_args):
        self.clear_draft()
        self.after_request()

    def clear_draft(self, *_args):
        self.draft_data = None
        self.body.clear()
        self.body.setReadOnly(True)
        self.subject.clear()
        self.subject.hide()
        self.draft_label.setText('O rascunho aparecerá aqui para você revisar.')
        self.copy_button.setEnabled(False)
        self.copy_recipient.setEnabled(False)

    def prepare_draft(self):
        item = self.suggestions.currentItem()
        if not item or not item.data(Qt.ItemDataRole.UserRole)['purpose'] or self.busy:
            return
        self.clear_draft()
        self.request('calendar.draft', {**self.arguments, 'purpose': item.data(Qt.ItemDataRole.UserRole)['purpose'],
            'channel': self.channel.currentData()}, self.draft_loaded)

    def draft_loaded(self, data):
        self.draft_data = data
        self.draft_label.setText(f"{'WhatsApp' if data['channel'] == 'whatsapp' else 'E-mail'} para {data['name']} · {data['recipient']}")
        self.subject.setText(data['subject'])
        self.subject.setVisible(data['channel'] == 'email')
        self.body.setPlainText(data['body'])
        self.body.setReadOnly(False)
        self.status.setText(data['message'])

    def copy_message(self):
        if self.draft_data and self.body.toPlainText().strip():
            text = (self.subject.text() + '\n\n' if self.draft_data['channel'] == 'email' else '') + self.body.toPlainText()
            QApplication.clipboard().setText(text)
            self.status.setText('Mensagem copiada. Nenhuma mensagem foi enviada.')

    def copy_destination(self):
        if self.draft_data:
            QApplication.clipboard().setText(self.draft_data['recipient'])
            self.status.setText('Destinatário copiado. Nenhuma mensagem foi enviada.')

    def manage_contacts(self):
        self.clear_draft()
        self.contacts_window = contacts_dialog(self.client, self)
        self.contacts_window.finished.connect(lambda _result: self.refresh())
        self.contacts_window.open()

    def after_request(self):
        if not hasattr(self, 'ai_button'):
            return
        self.ai_button.setEnabled(not self.busy and self.data.get('ai_available', False))
        item = self.suggestions.currentItem()
        self.draft_button.setEnabled(not self.busy and bool(item and item.data(Qt.ItemDataRole.UserRole)['purpose'])
            and bool(self.arguments['contact_key']))
        self.copy_button.setEnabled(not self.busy and self.draft_data is not None)
        self.copy_recipient.setEnabled(not self.busy and self.draft_data is not None)


def assistant_dialog(client, alias, period, event, parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle('Sugestões para o compromisso')
    dialog.resize(700, 700)
    dialog.setMinimumSize(540, 420)
    layout = QVBoxLayout(dialog)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    dialog.page = CalendarAssistantPage(client, alias, period, event, dialog)
    scroll.setWidget(dialog.page)
    layout.addWidget(scroll)
    close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    close.button(QDialogButtonBox.StandardButton.Close).setText('Fechar')
    close.rejected.connect(dialog.reject)
    layout.addWidget(close)
    dialog.page.refresh()
    return dialog
