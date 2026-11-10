"""Attention inbox, monitoring controls and explicit message review."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from jarvis.ui.administration import AdministrativePage, literal_label
from jarvis.application.calendar_assistant import MESSAGE_TEMPLATES


def caption(text, size=20):
    label = literal_label(text)
    label.setStyleSheet(f'font-size: {size}px; font-weight: 600; color: #dff3f5;')
    return label


def spin(minimum, maximum, value):
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


class MessageReviewPage(AdministrativePage):
    def __init__(self, client, row, completed, parent=None):
        super().__init__(client, parent)
        self.row, self.completed, self.preview = row, completed, None
        self.finished = False
        self.closed = False
        self.layout.addWidget(caption('Revise antes de continuar'))
        self.layout.addWidget(literal_label(row['event']['title']))
        self.destination = literal_label('Carregando destinatário…')
        self.layout.addWidget(self.destination)
        self.channel = QComboBox()
        self.channel.addItem('WhatsApp primeiro; e-mail se não houver WhatsApp', 'auto')
        self.channel.addItem('WhatsApp', 'whatsapp')
        self.channel.addItem('E-mail', 'email')
        self.channel.currentIndexChanged.connect(lambda _index: self.refresh())
        self.layout.addWidget(self.channel)
        self.subject = QLineEdit()
        self.subject.setMaxLength(240)
        self.subject.setAccessibleName('Assunto da mensagem')
        self.layout.addWidget(self.subject)
        self.body = QPlainTextEdit()
        self.body.setAccessibleName('Mensagem para aprovação')
        self.layout.addWidget(self.body, 1)
        self.notice = literal_label('Aprovação vale para o destinatário e texto exibidos. Nenhum envio aconteceu ainda.')
        self.layout.addWidget(self.notice)
        self.approve_button = QPushButton('Aprovar esta mensagem')
        self.approve_button.setObjectName('primaryAction')
        self.approve_button.clicked.connect(self.approve)
        self.layout.addWidget(self.approve_button)
        self.controls = [self.channel, self.subject, self.body, self.approve_button]
        self.approve_button.setEnabled(False)

    def arguments(self):
        return {'id': self.row['id'], 'revision': self.row['revision'], 'channel': self.channel.currentData(),
                'subject': self.subject.text(), 'body': self.body.toPlainText()}

    def refresh(self):
        if not self.busy and not self.finished:
            self.request('workflow.prepare', self.arguments(), self.loaded)

    def loaded(self, data):
        if self.closed:
            self.client.submit('workflow.cancel', {'token': data['token']}, lambda _event: None)
            return
        self.preview = data
        draft = data['draft']
        self.destination.setText(f"{'WhatsApp' if draft['channel'] == 'whatsapp' else 'E-mail'} para {draft['name']} · {draft['recipient']}")
        self.subject.setText(draft['subject'])
        self.subject.setVisible(draft['channel'] == 'email')
        self.body.setPlainText(draft['body'])
        self.approve_button.setText(data['action'])
        self.notice.setText(data['notice'])
        self.status.setText('Revise o texto e o destinatário. Fechar cancela esta aprovação.')

    def approve(self):
        if self.busy or not self.preview or self.finished or self.closed:
            return
        expected = self.preview['draft']
        arguments = self.arguments()
        def prepared(data):
            if self.closed:
                self.client.submit('workflow.cancel', {'token': data['token']}, lambda _event: None)
                return
            if data['draft']['recipient'] != expected['recipient'] or data['draft']['channel'] != expected['channel']:
                self.loaded(data)
                self.status.setText('O destinatário ou canal mudou. Revise e aprove novamente.')
                return
            self.preview = data
            self.notice.setText('Aprovação enviada. Fechar não desfaz uma operação já iniciada; o resultado ficará no histórico.')
            self.request('workflow.approve', {'token': data['token']}, self.sent)
        self.request('workflow.prepare', arguments, prepared)

    def sent(self, result):
        self.finished = True
        self.status.setText(result['message'])
        self.notice.setText('Confira o resultado acima. Não haverá repetição automática desta operação.')
        self.completed()

    def after_request(self):
        self.approve_button.setEnabled(not self.busy and bool(self.preview) and not self.finished and not self.closed)

    def cancel(self):
        self.closed = True
        if self.preview and not self.finished:
            self.client.submit('workflow.cancel', {'token': self.preview['token']}, lambda _event: None)
        self.preview = None


class WorkflowInbox(AdministrativePage):
    def __init__(self, client, navigate, parent=None):
        super().__init__(client, parent)
        self.navigate = navigate
        self.data = {}
        self.review_dialog = None
        self.link_dialog = None
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.removeWidget(self.status)
        self.layout.addWidget(caption('Precisa da sua atenção', 25))
        self.layout.addWidget(literal_label('Propostas prontas para você decidir. Sua agenda continua sob seu controle.'))
        actions = QHBoxLayout()
        self.scan_button = QPushButton('Verificar agenda agora')
        self.scan_button.clicked.connect(lambda: self.request('workflow.scan', {}, self.loaded))
        self.settings_button = QPushButton('Automações')
        self.settings_button.clicked.connect(lambda: navigate('Automações'))
        self.pause_button = QPushButton('Pausar')
        self.pause_button.clicked.connect(self.pause)
        for widget in (self.scan_button, self.settings_button, self.pause_button):
            actions.addWidget(widget)
        self.layout.addLayout(actions)
        self.layout.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.content.setObjectName('workflowCards')
        self.content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.content.setStyleSheet('QWidget#workflowCards { background: #07111b; } QFrame#proposalCard { background: #102330; border: 1px solid #2d4859; border-radius: 12px; }')
        self.cards = QVBoxLayout(self.content)
        self.cards.setContentsMargins(0, 6, 6, 0)
        self.cards.setSpacing(14)
        scroll.setWidget(self.content)
        self.layout.addWidget(scroll, 1)
        self.controls = [self.scan_button, self.pause_button]
        self.card_buttons = []
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() and not self.busy else None)

    def connected(self):
        self.timer.start()
        self.refresh()

    def disconnected(self):
        self.timer.stop()
        if self.review_dialog:
            self.review_dialog.reject()
        self.clear_cards()
        self.data = {}
        self.status.setText('JARVIS desconectado. Nenhum pedido será reenviado.')

    def clear_cards(self):
        self.controls = [self.scan_button, self.pause_button]
        self.card_buttons = []
        while self.cards.count():
            item = self.cards.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def refresh(self):
        self.request('workflow.list', {}, self.loaded)

    def loaded(self, data):
        self.data = data
        callback = getattr(self, 'snapshot_loaded', None)
        if callable(callback):
            callback(data)
        self.clear_cards()
        enabled = data['settings']['enabled']
        self.pause_button.setVisible(enabled)
        self.status.setText(data['error'] or ('Acompanhamento ativado · atualiza em segundo plano.' if enabled else
            'Acompanhamento pausado. Ative em Automações ou verifique a agenda agora.'))
        if not data['rows']:
            self.cards.addWidget(literal_label('Nenhuma proposta pendente. Novos compromissos e mudanças aparecerão aqui quando o acompanhamento estiver ativo.'))
        for row in data['rows']:
            card = QFrame()
            card.setObjectName('proposalCard')
            layout = QVBoxLayout(card)
            layout.setContentsMargins(18, 16, 18, 16)
            start = datetime.fromisoformat(row['event']['start'])
            layout.addWidget(literal_label(f"{start:%d/%m às %H:%M} · {row['state_label']}"))
            layout.addWidget(caption(row['event']['title'], 18))
            layout.addWidget(literal_label(row['question']))
            layout.addWidget(literal_label(row['reason']))
            if row.get('insights'):
                layout.addWidget(literal_label('Para preparar: ' + ' '.join(row['insights'])))
            if row['changed']:
                layout.addWidget(literal_label('O compromisso ou contato mudou. Revise novamente antes de agir.'))
            if row['draft']:
                layout.addWidget(literal_label(f"{'WhatsApp' if row['draft']['channel'] == 'whatsapp' else 'E-mail'} · {row['draft']['name']}"))
                layout.addWidget(literal_label(row['draft']['body']))
            buttons = QGridLayout()
            index = 0
            if row['state'] in {'handed_off', 'result_unknown'}:
                pairs = [('Eu confirmei o envio', 'sent'), ('Verifiquei: não foi enviado', 'not_sent')]
                for label, action in pairs:
                    button = QPushButton(label)
                    button.clicked.connect(lambda _checked=False, item=row, action=action: self.decide(item, action))
                    buttons.addWidget(button, index // 2, index % 2)
                    index += 1
                    self.card_buttons.append(button)
            else:
                needs_email = bool(row['draft'] and row['draft']['channel'] == 'email' and not data['channels']['email_ready'])
                review = QPushButton('Configurar envio por e-mail' if needs_email else 'Revisar mensagem' if row['purpose'] else 'Ver sugestões')
                review.setObjectName('primaryAction')
                review.clicked.connect(lambda _checked=False, item=row, setup=needs_email: self.configure_email() if setup else self.review(item) if item['purpose'] else self.details(item))
                buttons.addWidget(review, 0, 0)
                index = 1
                self.card_buttons.append(review)
                contact = QPushButton('Trocar contato' if row['contact_key'] else 'Vincular contato')
                contact.clicked.connect(lambda _checked=False, item=row: self.link(item))
                buttons.addWidget(contact, index // 2, index % 2)
                index += 1
                self.card_buttons.append(contact)
                for label, action in [('Adiar 1 h', 'snooze'), ('Ignorar', 'ignore')]:
                    button = QPushButton(label)
                    button.clicked.connect(lambda _checked=False, item=row, action=action: self.decide(item, action))
                    buttons.addWidget(button, index // 2, index % 2)
                    index += 1
                    self.card_buttons.append(button)
            layout.addLayout(buttons)
            self.cards.addWidget(card)
        self.cards.addStretch(1)
        self.controls += self.card_buttons

    def decide(self, row, action):
        if action in {'sent', 'not_sent'}:
            dialog = QDialog(self)
            dialog.setWindowTitle('Registrar sua verificação')
            layout = QVBoxLayout(dialog)
            layout.addWidget(literal_label('Você conferiu no aplicativo e confirma que esta mensagem ' +
                ('foi enviada?' if action == 'sent' else 'não foi enviada? Isso libera uma nova revisão manual.')))
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Yes).setText('Confirmo minha verificação')
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Cancelar')
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.accepted.connect(lambda: self.request('workflow.action', {'id': row['id'], 'revision': row['revision'], 'action': action}, self.loaded))
            self.link_dialog = dialog
            dialog.open()
        else:
            self.request('workflow.action', {'id': row['id'], 'revision': row['revision'], 'action': action}, self.loaded)

    def configure_email(self):
        self.navigate('Automações')
        window = self.window()
        if hasattr(window, 'workflow_page'):
            window.workflow_page.tabs.setCurrentIndex(1)

    def details(self, row):
        from jarvis.ui.calendar_assistant import assistant_dialog
        if self.link_dialog:
            self.link_dialog.reject()
        self.link_dialog = assistant_dialog(self.client, row['alias'], 'upcoming', row['event'], self)
        self.link_dialog.open()

    def pause(self):
        if self.data:
            self.request('workflow.settings', {'settings': {**self.data['settings'], 'enabled': False},
                'revision': self.data['revision']}, self.loaded)

    def review(self, row):
        if self.busy:
            return
        if self.review_dialog:
            self.review_dialog.reject()
        callback = getattr(self, 'review_requested', None)
        if callable(callback):
            self.review_dialog = callback(row)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Aprovar mensagem do compromisso')
        dialog.resize(650, 540)
        layout = QVBoxLayout(dialog)
        page = MessageReviewPage(self.client, row, self.refresh, dialog)
        dialog.page = page
        layout.addWidget(page)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText('Fechar')
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.finished.connect(lambda _result: page.cancel())
        self.review_dialog = dialog
        page.refresh()
        dialog.open()

    def link(self, row):
        dialog = QDialog(self)
        dialog.setWindowTitle('Vincular o contato certo')
        layout = QVBoxLayout(dialog)
        layout.addWidget(literal_label('Quem participa deste compromisso? O vínculo ficará salvo para este evento.'))
        choices = QComboBox()
        choices.addItem('Escolha um contato…', '')
        for person in self.data.get('contacts', []):
            choices.addItem(f"{person['name']} · {person['email'] or person['whatsapp']}", 'local:' + person['id'])
        layout.addWidget(choices)
        help_button = QPushButton('Cadastrar ou editar contatos')
        help_button.clicked.connect(lambda: self.navigate('Agenda'))
        layout.addWidget(help_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Vincular este contato')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Cancelar')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.accepted.connect(lambda: self.request('workflow.link', {'id': row['id'], 'revision': row['revision'],
            'contact_key': choices.currentData()}, self.loaded))
        self.link_dialog = dialog
        dialog.open()


class WorkflowSettingsPage(AdministrativePage):
    def __init__(self, client, navigate, parent=None):
        super().__init__(client, parent)
        self.data = {}
        self.rule_id = None
        self.confirmation = None
        self.layout.setContentsMargins(26, 22, 26, 22)
        self.layout.addWidget(caption('Automações da agenda', 28))
        tabs = QTabWidget()
        self.layout.addWidget(tabs, 1)
        monitoring, channels, rules, history = QWidget(), QWidget(), QWidget(), QWidget()
        for widget, title in ((monitoring, 'Acompanhamento'), (channels, 'Mensagens'), (rules, 'Regras autorizadas'), (history, 'Histórico')):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            widget.setObjectName('workflowSettingsContent')
            widget.setStyleSheet('QWidget#workflowSettingsContent { background: #0a1823; }')
            tabs.addTab(scroll, title)
        self.tabs = tabs
        m = QVBoxLayout(monitoring)
        m.addWidget(literal_label('O JARVIS acompanha somente a agenda selecionada, enquanto estiver aberto. Não cria tarefas no Windows nem inicia ao ligar o computador.'))
        self.enabled = QCheckBox('Acompanhar minha agenda em segundo plano')
        self.ai = QCheckBox('Usar IA local somente em compromissos novos ou alterados')
        m.addWidget(self.enabled)
        form = QFormLayout()
        self.mode, self.alias = QComboBox(), QComboBox()
        for name, value in [('Somente sugestões', 'suggest'), ('Preparar e aprovar', 'prepare'), ('Usar minhas regras autorizadas', 'rules')]:
            self.mode.addItem(name, value)
        self.interval, self.horizon = spin(5, 60, 5), spin(1, 168, 48)
        self.quiet_start, self.quiet_end = spin(0, 23, 22), spin(0, 23, 7)
        self.timezone = QLineEdit('America/Sao_Paulo')
        for title, widget in [('Modo', self.mode), ('Agenda autorizada', self.alias), ('Consultar a cada (min)', self.interval),
                ('Olhar os próximos (horas)', self.horizon), ('Silêncio a partir das (h)', self.quiet_start),
                ('Silêncio até as (h)', self.quiet_end), ('Fuso horário', self.timezone)]:
            form.addRow(title, widget)
        m.addLayout(form)
        m.addWidget(self.ai)
        self.save_button = QPushButton('Salvar acompanhamento')
        self.save_button.setObjectName('primaryAction')
        self.save_button.clicked.connect(self.save)
        m.addWidget(self.save_button)
        permissions = QPushButton('Abrir permissões de mensagens')
        permissions.clicked.connect(lambda: navigate('Permissões'))
        m.addWidget(permissions)
        m.addStretch(1)
        c = QVBoxLayout(channels)
        c.addWidget(caption('WhatsApp', 18))
        c.addWidget(literal_label('Prioridade quando o contato tiver WhatsApp. Após sua aprovação, abre a conversa com o texto pronto. Você conclui o envio no aplicativo. Nenhuma janela será aberta por regras em segundo plano.'))
        c.addWidget(caption('E-mail pela sua conta', 18))
        c.addWidget(literal_label('Opcional: informe os dados SMTP fornecidos pelo seu provedor. Use uma senha de aplicativo quando o provedor exigir. O login da agenda Google não concede acesso ao e-mail.'))
        smtp_form = QFormLayout()
        self.host, self.username, self.sender, self.password = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.port = QComboBox()
        self.port.addItem('465 — TLS', 465)
        self.port.addItem('587 — STARTTLS', 587)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Senha; vazio mantém a já salva')
        for title, widget in [('Servidor SMTP', self.host), ('Conexão protegida', self.port), ('Usuário', self.username),
                              ('Endereço de envio', self.sender), ('Senha protegida', self.password)]:
            smtp_form.addRow(title, widget)
        c.addLayout(smtp_form)
        self.channel_state = literal_label('Nenhuma conta de envio configurada.')
        c.addWidget(self.channel_state)
        self.channel_save = QPushButton('Salvar conta de envio')
        self.channel_save.clicked.connect(self.save_channel)
        self.channel_remove = QPushButton('Desconectar envio por e-mail')
        self.channel_remove.clicked.connect(self.remove_channel)
        c.addWidget(self.channel_save)
        c.addWidget(self.channel_remove)
        c.addStretch(1)
        r = QVBoxLayout(rules)
        r.addWidget(literal_label('Regras podem enviar e-mails sem nova pergunta dentro dos limites aprovados. WhatsApp continua exigindo conclusão manual. Alterações do compromisso voltam para revisão.'))
        self.rules = QListWidget()
        self.rules.setMaximumHeight(130)
        self.rules.currentItemChanged.connect(self.select_rule)
        r.addWidget(self.rules)
        rule_form = QFormLayout()
        self.rule_contact, self.rule_purpose, self.rule_channel = QComboBox(), QComboBox(), QComboBox()
        self.rule_purpose.addItem('Confirmar compromisso pendente', 'confirm')
        self.rule_purpose.addItem('Lembrar do compromisso', 'notify')
        self.rule_channel.addItem('WhatsApp primeiro (fica para aprovação)', 'auto')
        self.rule_channel.addItem('E-mail automático', 'email')
        self.rule_lead, self.rule_limit = spin(15, 1440, 1440), spin(1, 5, 1)
        self.rule_start, self.rule_end = spin(0, 23, 9), spin(1, 24, 18)
        for title, widget in [('Contato específico', self.rule_contact), ('Mensagem', self.rule_purpose), ('Canal', self.rule_channel),
                ('Antecedência máxima (min)', self.rule_lead), ('Máximo por contato/dia', self.rule_limit),
                ('Enviar a partir das (h)', self.rule_start), ('Enviar até as (h)', self.rule_end)]:
            rule_form.addRow(title, widget)
        r.addLayout(rule_form)
        self.rule_preview = literal_label('Modelo: Olá, {contato}! Podemos confirmar nosso compromisso “{evento}”, em {data e hora}?')
        self.rule_purpose.currentIndexChanged.connect(self.update_template)
        r.addWidget(self.rule_preview)
        self.rule_enabled = QCheckBox('Ativar esta regra')
        self.rule_accept = QCheckBox('Autorizo este modelo de mensagem para o contato, canal e limites acima')
        r.addWidget(self.rule_enabled)
        r.addWidget(self.rule_accept)
        r.addWidget(literal_label('Limite adicional: até 5 tentativas automáticas por dia no total. Nenhum reenvio automático após uma tentativa; resultados incertos exigem verificação.'))
        buttons = QHBoxLayout()
        self.rule_new, self.rule_save, self.rule_delete = QPushButton('Nova regra'), QPushButton('Salvar regra'), QPushButton('Remover regra')
        self.rule_new.clicked.connect(self.new_rule)
        self.rule_save.clicked.connect(self.save_rule)
        self.rule_delete.clicked.connect(self.delete_rule)
        for button in (self.rule_new, self.rule_save, self.rule_delete):
            buttons.addWidget(button)
        r.addLayout(buttons)
        r.addStretch(1)
        h = QVBoxLayout(history)
        h.addWidget(literal_label('Aceita pelo servidor não significa entregue ou lida. “Envio informado por você” é uma confirmação manual.'))
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(['Quando', 'Compromisso', 'Contato', 'Resultado'])
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history.verticalHeader().hide()
        h.addWidget(self.history, 1)
        self.controls = [self.enabled, self.ai, self.mode, self.alias, self.interval, self.horizon, self.quiet_start,
            self.quiet_end, self.timezone, self.save_button, self.host, self.username, self.sender, self.password,
            self.channel_save, self.channel_remove, self.port, self.rules, self.rule_contact, self.rule_purpose, self.rule_channel,
            self.rule_lead, self.rule_limit, self.rule_start, self.rule_end, self.rule_enabled, self.rule_accept,
            self.rule_new, self.rule_save, self.rule_delete]

    def refresh(self):
        self.request('workflow.list', {}, self.loaded)

    def loaded(self, data):
        self.data = data
        settings = data['settings']
        self.enabled.setChecked(settings['enabled'])
        self.ai.setChecked(settings['use_ai'])
        self.mode.setCurrentIndex(self.mode.findData(settings['mode']))
        self.alias.clear()
        for alias in data['calendars']:
            self.alias.addItem(alias, alias)
        self.alias.setCurrentIndex(self.alias.findData(settings['alias']))
        for widget, name in ((self.interval, 'interval_minutes'), (self.horizon, 'horizon_hours'),
                             (self.quiet_start, 'quiet_start'), (self.quiet_end, 'quiet_end')):
            widget.setValue(settings[name])
        self.timezone.setText(settings['timezone'])
        smtp = data['channels']['email']
        for widget, name in ((self.host, 'host'), (self.username, 'username'), (self.sender, 'sender')):
            widget.setText(smtp.get(name, ''))
        self.password.clear()
        self.port.setCurrentIndex(self.port.findData(smtp.get('port', 465)))
        self.channel_state.setText('Conta configurada. A autenticação será verificada no envio aprovado.' if data['channels']['email_ready'] else 'Nenhuma conta de envio configurada.')
        self.rule_contact.clear()
        self.rule_contact.addItem('Escolha um contato…', '')
        for contact in data['contacts']:
            self.rule_contact.addItem(f"{contact['name']} · {contact['email'] or contact['whatsapp']}", 'local:' + contact['id'])
        self.rules.clear()
        for rule in data['rules']:
            name = self.rule_contact.itemText(self.rule_contact.findData(rule['contact_key']))
            state = 'Revisar contato, conta ou agenda' if rule['requires_review'] else 'Ativa' if rule['enabled'] else 'Pausada'
            item = QListWidgetItem(f"{state} · {name} · {'confirmar' if rule['purpose'] == 'confirm' else 'lembrar'}")
            item.setData(Qt.ItemDataRole.UserRole, rule)
            self.rules.addItem(item)
        self.new_rule()
        records = list(reversed(data['history']))
        self.history.setRowCount(len(records))
        for index, record in enumerate(records):
            at = datetime.fromisoformat(record['at']).astimezone(ZoneInfo(data['settings']['timezone'])).strftime('%d/%m %H:%M')
            for column, value in enumerate((at, record['title'], record['contact'], record['message'])):
                self.history.setItem(index, column, QTableWidgetItem(value))
        self.status.setText(data['error'] or data['note'])

    def save(self):
        settings = {'enabled': self.enabled.isChecked(), 'use_ai': self.ai.isChecked(),
            'mode': self.mode.currentData(), 'alias': self.alias.currentData() or self.data['settings']['alias'],
            'interval_minutes': self.interval.value(), 'horizon_hours': self.horizon.value(),
            'quiet_start': self.quiet_start.value(), 'quiet_end': self.quiet_end.value(), 'timezone': self.timezone.text()}
        self.request('workflow.settings', {'settings': settings, 'revision': self.data['revision']}, self.loaded)

    def save_channel(self):
        smtp = {'host': self.host.text().strip(), 'port': self.port.currentData(), 'username': self.username.text().strip(),
                'sender': self.sender.text().strip(), 'password': self.password.text()}
        self.request('workflow.channel_save', {'smtp': smtp, 'revision': self.data['channels']['revision']}, lambda _data: self.refresh())

    def remove_channel(self):
        self.confirm('Desconectar o envio por e-mail? As propostas continuam disponíveis.',
            lambda: self.request('workflow.channel_save', {'smtp': None, 'revision': self.data['channels']['revision']}, lambda _data: self.refresh()))

    def select_rule(self, item, _previous=None):
        if item is None:
            return
        rule = item.data(Qt.ItemDataRole.UserRole)
        self.rule_id = rule['id']
        for widget, key in ((self.rule_contact, 'contact_key'), (self.rule_purpose, 'purpose'), (self.rule_channel, 'channel')):
            widget.setCurrentIndex(widget.findData(rule[key]))
        for widget, key in ((self.rule_lead, 'lead_minutes'), (self.rule_limit, 'daily_limit'), (self.rule_start, 'start_hour'), (self.rule_end, 'end_hour')):
            widget.setValue(rule[key])
        self.rule_enabled.setChecked(rule['enabled'])
        self.rule_accept.setChecked(False)

    def new_rule(self):
        self.rule_id = None
        self.rules.setCurrentRow(-1)
        self.rule_contact.setCurrentIndex(0)
        self.rule_enabled.setChecked(False)
        self.rule_accept.setChecked(False)

    def update_template(self):
        template = MESSAGE_TEMPLATES[self.rule_purpose.currentData()]
        self.rule_preview.setText('Modelo: ' + template.format(name='{contato}', reference='“{evento}”, em {data e hora}'))
        self.rule_accept.setChecked(False)

    def save_rule(self):
        rule = {'id': self.rule_id or uuid4().hex, 'enabled': self.rule_enabled.isChecked(),
            'contact_key': self.rule_contact.currentData(), 'purpose': self.rule_purpose.currentData(),
            'channel': self.rule_channel.currentData(), 'lead_minutes': self.rule_lead.value(),
            'daily_limit': self.rule_limit.value(), 'start_hour': self.rule_start.value(), 'end_hour': self.rule_end.value()}
        self.request('workflow.rule', {'rule': rule, 'revision': self.data['revision'], 'accepted': self.rule_accept.isChecked()}, self.loaded)

    def delete_rule(self):
        if self.rule_id:
            values = {'id': self.rule_id, 'revision': self.data['revision']}
            self.confirm('Remover esta regra? Novos envios deixam de ser autorizados por ela.',
                lambda: self.request('workflow.rule_delete', values, self.loaded))

    def confirm(self, text, accepted):
        dialog = QDialog(self)
        dialog.setWindowTitle('Confirmar alteração')
        layout = QVBoxLayout(dialog)
        layout.addWidget(literal_label(text))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText('Confirmar')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Cancelar')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.accepted.connect(accepted)
        self.confirmation = dialog
        dialog.open()
