"""Local account widgets; OAuth via typed administration, writes via text executor."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QDateTime, QTime, QTimeZone
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from jarvis.integrations.google.accounts import LABELS
from jarvis.integrations.google.public_login import PUBLIC_CLIENT
from jarvis.ui.administration import AdministrativePage, literal_label


class AccountsPage(AdministrativePage):
    def __init__(self, client, submit_command, parent=None):
        super().__init__(client, parent)
        self.submit_command = submit_command
        self.data, self.selected, self.choices = {}, None, []
        self.confirmation = None
        self._calendar_timezone_valid = False
        self.setStyleSheet('''
            QLabel { color: #c9dfe8; font-size: 13px; }
            QWidget#calendarSetup, QWidget#accountSettings { background: #0a1925; }
            QScrollArea { border: none; background: #0a1925; }
            QTabWidget::pane { border: 1px solid #29495b; background: #0a1925; }
            QTabBar::tab { background: #102736; color: #b8d3df; padding: 8px 14px; }
            QTabBar::tab:selected { background: #17445a; color: #edf7fa; }
            QLineEdit, QComboBox, QDateTimeEdit, QListWidget {
                min-height: 28px; padding: 4px; background: #07131e;
                color: #e2edf2; border: 1px solid #355567; border-radius: 6px;
            }
            QPushButton { padding: 7px 12px; background: #123548; color: #e2edf2;
                          border: 1px solid #355567; border-radius: 6px; }
            QPushButton:disabled { color: #7891a0; background: #101c25; }
            QPushButton:checked { background: #1e4c60; }
            QCheckBox { color: #c9dfe8; padding: 4px; }
        ''')
        self.layout.addWidget(literal_label('Conecte somente os serviços que quiser usar. '
            'Sua conta, suas agendas e suas permissões são individuais.'))
        tabs = QTabWidget()
        from jarvis.ui.delegation import DelegationPage
        self.delegation = DelegationPage(client, submit_command)
        delegation_scroll = QScrollArea()
        delegation_scroll.setWidgetResizable(True)
        delegation_scroll.setWidget(self.delegation)
        self.layout.addWidget(tabs)
        accounts, calendar = QWidget(), QWidget()
        accounts.setObjectName('accountSettings')
        calendar.setObjectName('calendarSetup')
        a, c = QVBoxLayout(accounts), QVBoxLayout(calendar)
        a.setAlignment(Qt.AlignmentFlag.AlignTop)
        c.setAlignment(Qt.AlignmentFlag.AlignTop)
        for content, title in ((calendar, 'Agenda'), (accounts, 'Gerenciar contas')):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            tabs.addTab(scroll, title)
        tabs.addTab(delegation_scroll, 'OpenAI / Gemini')
        # Google errors describe the Google tabs, not the independent API panel.
        tabs.currentChanged.connect(lambda index: self.status.setVisible(index != 2))
        self.refresh_button = QPushButton('Atualizar conexões e agendas')
        self.refresh_button.clicked.connect(self.refresh)
        a.addWidget(self.refresh_button)
        self.rows = QListWidget()
        self.rows.currentItemChanged.connect(self.select)
        a.addWidget(self.rows, 1)
        self.details = literal_label('Selecione uma conexão. Tokens e chaves não são exibidos.')
        a.addWidget(self.details)
        self.label = QLineEdit()
        self.label.setPlaceholderText('Nome amigável escolhido por você; pessoal/trabalho não é inferido')
        a.addWidget(self.label)
        self.operation = QComboBox()
        for operation in ('calendar_read', 'calendar_write', 'drive_docs_read'):
            self.operation.addItem(LABELS[operation], operation)
        a.addWidget(self.operation)
        self.add_button = QPushButton('Adicionar conexão — ainda sem OAuth ou permissões')
        self.add_button.clicked.connect(self.add)
        a.addWidget(self.add_button)
        actions = QHBoxLayout()
        self.connect_button = QPushButton('Conectar/reconectar…')
        self.test_button = QPushButton('Testar — somente leitura')
        self.default_button = QPushButton('Definir padrão desta operação')
        self.rename_button = QPushButton('Salvar nome')
        self.disconnect_button = QPushButton('Desconectar localmente…')
        self.connect_button.clicked.connect(self.connect_account)
        self.custom_client_button = QPushButton('Usar cliente Google próprio…')
        self.custom_client_button.clicked.connect(lambda: self.connect_account(custom_client=True))
        self.test_button.clicked.connect(lambda: self.account_request('accounts.test'))
        self.default_button.clicked.connect(lambda: self.change('default', True))
        self.rename_button.clicked.connect(lambda: self.change('label', self.label.text()))
        self.disconnect_button.clicked.connect(self.disconnect_account)
        for button in (self.connect_button, self.test_button, self.default_button):
            actions.addWidget(button)
        a.addLayout(actions)
        secondary = QHBoxLayout()
        secondary.addWidget(self.rename_button)
        secondary.addWidget(self.disconnect_button)
        a.addLayout(secondary)
        a.addWidget(self.custom_client_button)
        a.addWidget(literal_label('Desconectar remove somente a credencial local e invalida aprovações. '
            'Não desfaz efeitos nem revoga no Google. Revogação remota pode afetar Calendar, Drive/Docs e outros '
            'clientes do mesmo projeto OAuth; use a gestão oficial do Google conscientemente. '
            'Spotify OAuth e redes sociais: planejados, sem backend de contas pronto.'))
        c.addWidget(literal_label('1. Conta Google'))
        self._manual_setup_help = (
            'Nesta prévia, o login precisa de um arquivo OAuth do tipo Desktop, criado no Google Cloud. '
            'Esse arquivo identifica o aplicativo; não é sua senha nem substitui o login no Google. '
            'O instalador ainda não inclui um cliente de login público pronto. '
            'Sem esse arquivo, você pode continuar usando o assistente local. '
            'Instruções: docs/manual/GOOGLE.md no repositório.')
        self.setup_help = literal_label(self._manual_setup_help)
        c.addWidget(self.setup_help)
        self.account_summary = literal_label('Verificando a conexão existente…')
        c.addWidget(self.account_summary)
        self.guided_connect = QPushButton('Conectar Google para consultas…')
        self.guided_connect.clicked.connect(self.connect_candidate)
        c.addWidget(self.guided_connect)
        self.guided_refresh = QPushButton('Atualizar conta e agendas')
        self.guided_refresh.clicked.connect(self.refresh)
        c.addWidget(self.guided_refresh)
        c.addWidget(literal_label('2. Escolher agenda'))
        self.primary_button = QPushButton('Consultar minha agenda principal')
        self.primary_button.clicked.connect(self.load_primary)
        c.addWidget(self.primary_button)
        self.calendar_state = literal_label('Carregando nomes reais das agendas disponíveis…')
        c.addWidget(self.calendar_state)
        self.reader, self.writer = QComboBox(), QComboBox()
        a.addWidget(literal_label('Concessões separadas (não é necessário combiná-las no fluxo guiado):'))
        a.addWidget(self.reader)
        self.list_button = QPushButton('Consultar calendários — somente leitura')
        self.list_button.clicked.connect(self.list_calendars)
        a.addWidget(self.list_button)
        self.calendars = QComboBox()
        self.calendars.setPlaceholderText('Carregando agendas…')
        self.reader.currentIndexChanged.connect(self.clear_choices)
        c.addWidget(self.calendars)
        self.alias = QLineEdit('teste')
        self.alias.setPlaceholderText('Alias explícito do calendário')
        a.addWidget(literal_label('Alias técnico local; não é o nome da agenda:'))
        a.addWidget(self.alias)
        a.addWidget(self.writer)
        self.write_selection = QCheckBox('Selecionar este calendário para WRITE (não ativa escrita)')
        a.addWidget(self.write_selection)
        self.select_button = QPushButton('Salvar esta agenda')
        self.select_button.clicked.connect(self.select_calendar)
        c.addWidget(self.select_button)
        self.read_enabled = QCheckBox('Usar a agenda salva nas consultas — reinicie o JARVIS para aplicar')
        self.read_enabled.clicked.connect(lambda enabled: self.request('accounts.read', {'enabled': enabled}, self.render))
        c.addWidget(self.read_enabled)
        self.show_events = QPushButton('Mostrar opções para criar ou alterar eventos')
        self.show_events.setCheckable(True)
        c.addWidget(self.show_events)
        self.event_options = QWidget()
        events = QVBoxLayout(self.event_options)
        self.event_options.setVisible(False)
        self.show_events.toggled.connect(self.event_options.setVisible)
        c.addWidget(self.event_options)
        self.write_connect = QPushButton('Autorizar criação e alteração de eventos…')
        self.write_connect.clicked.connect(lambda: self.connect_operation('calendar_write'))
        events.addWidget(self.write_connect)
        self.save_events = QPushButton('Salvar esta agenda para criação e alteração')
        self.save_events.clicked.connect(lambda: self.select_calendar(for_events=True))
        events.addWidget(self.save_events)
        events.addWidget(literal_label('Esta seleção é separada da agenda de consultas. '
                                      'Salvar não ativa a criação de eventos.'))
        events.addWidget(literal_label('A criação de eventos permanece desligada até consentimento mínimo, calendário de teste escolhido '
            'e aprovação de uma prévia concreta. Ativação não ocorre ao salvar seleção. '
            'Somente criar evento simples ou alterar título/horário selecionado; sem convidados, recorrência, '
            'anexos, notificações ou exclusão. Confirmação ocorre na página Conversa pelo executor.'))
        self.action = QComboBox()
        for label, value in [('Criar evento simples', 'criar'), ('Alterar título selecionado', 'titulo'),
                             ('Alterar horário selecionado', 'horario')]:
            self.action.addItem(label, value)
        events.addWidget(self.action)
        events.addWidget(literal_label('3. Revisar evento'))
        self.title, self.event_id = QLineEdit('JARVIS — teste de integração'), QLineEdit()
        self.start, self.end = QDateTimeEdit(), QDateTimeEdit()
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat('dd/MM/yyyy HH:mm')
            edit.setTimeZone(QTimeZone.systemTimeZone())
            edit.setDateTime(QDateTime.currentDateTime().addDays(1))
            edit.setTime(QTime(14, 0))
        self.end.setDateTime(self.start.dateTime().addSecs(1800))
        self.timezone = literal_label(QTimeZone.systemTimeZone().id().data().decode())
        for edit, hint in ((self.title, 'Título explícito'),
                           (self.event_id, 'ID do evento explicitamente selecionado — somente alteração')):
            edit.setPlaceholderText(hint)
            events.addWidget(edit)
        for label, widget in (('Início', self.start), ('Fim', self.end), ('Fuso horário da agenda', self.timezone)):
            events.addWidget(literal_label(label))
            events.addWidget(widget)
        self.preview = literal_label('Escolha uma agenda para revisar o evento. Nada será criado ao revisar.')
        events.addWidget(self.preview)
        self.review_button = QPushButton('Revisar evento — sem criar')
        self.review_button.clicked.connect(self.review)
        events.addWidget(self.review_button)
        self.prepare_button = QPushButton('Preparar prévia e confirmação pelo executor')
        self.prepare_button.clicked.connect(self.prepare)
        events.addWidget(self.prepare_button)
        events.addWidget(literal_label('Se a escrita estiver desligada, este painel não a ativa. '
            'A configuração avançada e o reinício ainda são necessários nesta prévia. '
            'Cada evento exige confirmação na Conversa.'))
        self.controls = [self.refresh_button, self.add_button, self.connect_button, self.test_button,
            self.default_button, self.rename_button, self.disconnect_button, self.list_button,
            self.select_button, self.read_enabled, self.prepare_button, self.guided_connect,
            self.guided_refresh, self.primary_button, self.write_connect, self.save_events, self.show_events,
            self.review_button, self.calendars, self.title, self.start, self.end, self.action, self.event_id]
        self.calendars.currentIndexChanged.connect(self.calendar_changed)
        self.after_request()

    def refresh(self):
        self.delegation.refresh()
        def loaded(data):
            self.render(data)
            self.request('accounts.calendar_guide', {}, self.render)
        self.request('accounts.list', {}, loaded)

    def clear_choices(self):
        self.choices = []
        self.calendars.clear()
        self.after_request()

    def render(self, data):
        if 'rows' not in data:
            self.data, self.selected, self.choices = {}, None, []
            self.rows.clear()
            self.status.setText('Desconexão local concluída; recarregue e reinicie o runtime.')
            return
        selected = self.selected['id'] if self.selected else None
        self.data, self.selected, self.choices = data, None, []
        login = data.get('google_login', {})
        if login.get('available'):
            self.setup_help.setText('Login JARVIS incluído: clique em Conectar Google para consultas. '
                'O Google pode apresentar avisos e limites enquanto o app não for verificado. Você escolhe a conta e autoriza '
                'somente identificação e consulta; eventos não são alterados. '
                'Nenhum arquivo técnico é necessário neste pacote. Você pode continuar sem Google.')
        else:
            self.setup_help.setText(self._manual_setup_help)
        self.rows.clear()
        self.reader.clear()
        self.writer.clear()
        self.calendars.clear()
        for row in data['rows']:
            item = QListWidgetItem(f"Google · {row['label']} · {row['status']}" + (' · padrão' if row['default'] else ''))
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.rows.addItem(item)
            if row['operation'] == 'calendar_read':
                self.reader.addItem(row['label'], row['id'])
            elif row['operation'] == 'calendar_write':
                self.writer.addItem(row['label'], row['id'])
            if row['id'] == selected:
                self.rows.setCurrentItem(item)
        self.read_enabled.setChecked(data['read_enabled'])
        self.calendar_state.setText(f"READ={data['read_enabled']} · WRITE={data['write_enabled']}. "
            'Estado local; teste conexão para comprovar prontidão. Conta e calendários não migram por troca de padrão.')
        candidate = next((r for r in data['rows'] if r['id'] == data.get('candidate')), None)
        if candidate is None:
            candidate = next((r for r in data['rows'] if r['operation'] == 'calendar_read' and r['default']), None)
        if candidate:
            self.reader.setCurrentIndex(self.reader.findData(candidate['id']))
            if data.get('matching_writer'):
                self.writer.setCurrentIndex(self.writer.findData(data['matching_writer']))
            writer = next((r for r in data['rows'] if r['operation'] == 'calendar_write' and r['default']), None)
            self.account_summary.setText(f"Conexão: {candidate['label']}\nConta: {candidate['status']}. "
                f"Acesso de escrita: {writer['status'] if writer else 'indisponível'}. "
                'Uma conexão válida será reutilizada, sem novo login.')
        if 'calendars' in data:
            self.render_calendars({'calendars': data['calendars']})
            self.calendar_state.setText(f"{len(self.choices)} agenda(s) carregada(s). " + data['calendar_notice'])
        self.status.setText(data.get('calendar_notice', data['note']))
        self.after_request()

    def select(self, current, previous=None):
        self.selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        if self.selected:
            row = self.selected
            self.label.setText(row['label'])
            self.details.setText(f"{row['account']}\n{LABELS[row['operation']]} · {row['status']}\n"
                f"{row['reason']}\nScopes OAuth: {', '.join(row['scopes']) or 'nenhum'}. "
                f"Recursos operacionais: {', '.join(row['resources']) or 'nenhum'}. "
                f"Política efetiva: {json.dumps(row.get('policies', []), ensure_ascii=False)}. "
                'Bloqueio prevalece; WRITE sempre confirma; decisões finais em Permissões/executor.')
        self.after_request()

    def after_request(self):
        if not hasattr(self, 'prepare_button'):
            return
        for button in (self.connect_button, self.test_button, self.default_button,
                       self.rename_button, self.disconnect_button, self.custom_client_button):
            button.setEnabled(not self.busy and self.selected is not None)
        if self.selected and self.selected['operation'] in {'authentication', 'legacy_calendar_write'}:
            self.connect_button.setEnabled(False)
            self.custom_client_button.setEnabled(False)
        if self.selected and self.selected['status'] in {'conectado', 'indisponível'}:
            self.custom_client_button.setEnabled(False)
        self.custom_client_button.setToolTip('Adicione uma conexão sem autorização para usar um cliente próprio; '
            'uma conexão existente não é substituída silenciosamente.')
        self.prepare_button.setEnabled(not self.busy and bool(self.data.get('write_enabled')) and
            self.calendars.currentData() is not None)
        self.prepare_button.setToolTip('WRITE está desligado. Revise o evento; a ativação e a confirmação pelo executor são gates separados.'
            if not self.data.get('write_enabled') else 'A confirmação ocorrerá na página Conversa, antes da escrita.')
        self.select_button.setEnabled(not self.busy and self.calendars.currentData() is not None)
        self.select_button.setToolTip('Escolha uma agenda carregada da API; digitar um nome não seleciona um recurso.')
        self.review_button.setEnabled(not self.busy and self.calendars.currentData() is not None and self._calendar_timezone_valid)
        self.review_button.setToolTip('Escolha uma agenda com fuso confirmado para ver a prévia; revisar não cria eventos.')
        self.calendars.setEnabled(not self.busy and bool(self.choices))
        self.calendars.setToolTip('Abra a lista e escolha uma agenda. Nenhuma agenda é selecionada automaticamente.'
            if self.choices else self.data.get('calendar_notice', 'Aguarde o carregamento ou atualize a conexão.'))
        self.guided_connect.setEnabled(not self.busy and bool(self.data.get('rows')))
        reader = next((r for r in self.data.get('rows', [])
                       if r['operation'] == 'calendar_read' and r['default']), None)
        self.primary_button.setEnabled(not self.busy and bool(reader and reader['status'] == 'conectado'))
        self.read_enabled.setEnabled(not self.busy and (bool(self.data.get('read_enabled'))
            or bool(reader and reader['resources'] and reader['status'] == 'conectado')))
        self.setup_help.setVisible(not reader or reader['status'] != 'conectado')
        self.write_connect.setEnabled(not self.busy and bool(reader and reader['status'] == 'conectado'))
        choice = next((r for r in self.choices if r['id'] == self.calendars.currentData()), {})
        self.save_events.setEnabled(not self.busy and bool(self.data.get('matching_writer'))
                                    and bool(choice.get('write_candidate')))
        self.guided_connect.setToolTip('Reutiliza e verifica conexões válidas; login oficial somente quando necessário. '
            'Se indisponível, atualize os estados ou reconecte o runtime.')
        for button in (self.test_button, self.default_button, self.rename_button, self.disconnect_button):
            button.setToolTip('Selecione uma conexão na aba Avançado para esta ação.')

    def add(self):
        if self.data:
            self.request('accounts.add', {'operation': self.operation.currentData(), 'label': self.label.text(),
                                         'revision': self.data['revision']}, self.render)

    def account_request(self, method, extra=None):
        if self.selected and self.data and not self.busy:
            success = (self.read_connection_ready
                       if method in {'accounts.connect', 'accounts.test'} and self.selected['operation'] == 'calendar_read'
                       else self.render)
            self.request(method, {'connection': self.selected['id'], 'revision': self.data['revision'],
                                  **(extra or {})}, success)
            if method == 'accounts.connect' and self.busy:
                self.status.setText('Conclua o login no navegador e volte ao JARVIS. '
                    'Você tem até 10 minutos e pode cancelar no navegador. '
                    'Conectar não ativa consultas nem escrita automaticamente.')

    def read_connection_ready(self, data):
        self.render(data)
        # Connection replies are inventories, not the guided setup state.
        # Restore the guide before the first metadata/selection step, without
        # selecting a calendar or enabling either operational permission.
        self.request('accounts.calendar_guide', {}, self.render)

    def change(self, action, value):
        self.account_request('accounts.change', {'action': action, 'value': value})

    def connect_account(self, *, custom_client=False):
        if not self.selected:
            return
        if self.selected['status'] in {'conectado', 'indisponível'}:
            self.account_request('accounts.test')
            return
        login = self.data.get('google_login', {})
        if (not custom_client and login.get('available')
                and self.selected['operation'] in login.get('operations', [])):
            self.account_request('accounts.connect', {'client_config': PUBLIC_CLIENT})
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Selecionar cliente OAuth Desktop existente', '', 'JSON (*.json)')
        if path:
            self.account_request('accounts.connect', {'client_config': path})

    def disconnect_account(self):
        if not self.selected or self.confirmation:
            return
        connection, revision = self.selected['id'], self.data['revision']
        generation = self.client.generation
        dialog = QDialog(self)
        self.confirmation = dialog
        layout = QVBoxLayout(dialog)
        layout.addWidget(literal_label(f"Desconectar localmente {self.selected['label']}? "
            'Remove somente esta credencial; invalida seleções/aprovações e não revoga acesso no Google.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        def finished(result):
            self.confirmation = None
            if result == QDialog.DialogCode.Accepted:
                if self.client.generation != generation or self.data.get('revision') != revision:
                    self.status.setText('Confirmação antiga após reinício/mudança; nenhuma credencial removida.')
                    return
                self.request('accounts.change', {'connection': connection, 'revision': revision,
                    'action': 'disconnect', 'value': True}, self.render)
        dialog.finished.connect(finished)
        dialog.open()

    def list_calendars(self):
        if self.data and self.reader.currentData():
            self.request('accounts.calendars', {'connection': self.reader.currentData(), 'revision': self.data['revision']}, self.render_calendars)

    def render_calendars(self, data):
        self.choices = data['calendars']
        self.calendar_state.setText(f'{len(self.choices)} agenda(s) disponível(is). '
            'Escolha uma agenda da lista e salve sua seleção.')
        self.calendars.clear()
        for row in self.choices:
            availability = 'permite escrita limitada (ainda desativada)' if row.get('write_candidate') else 'somente leitura'
            self.calendars.addItem(f"{row['label']} · {availability}", row['id'])
        self.calendars.setCurrentIndex(-1)
        self.calendars.setPlaceholderText(f'Escolha uma agenda — {len(self.choices)} disponível(is)'
            if self.choices else 'Nenhuma agenda disponível — veja a explicação abaixo')
        self.status.setText('Agendas carregadas. Abra a lista e escolha pelo nome real; nada foi autorizado automaticamente.'
            if self.choices else 'Nenhuma agenda carregada. Confira a explicação e a próxima ação.')

    def select_calendar(self, for_events=False):
        if self.choices and self.calendars.currentData():
            guided = 'candidate' in self.data
            writer = (self.data.get('matching_writer') if for_events else None) if guided else (
                self.writer.currentData() if self.write_selection.isChecked() else None)
            if for_events and not writer:
                self.status.setText('Autorize a criação de eventos e atualize as agendas antes de salvar.')
                return
            if guided:
                item = next(r for r in self.choices if r['id'] == self.calendars.currentData())
                writer = writer if item.get('write_candidate') else None
            self.request('accounts.calendar_select', {'read_connection': self.reader.currentData(),
                'write_connection': writer,
                'revision': self.data['revision'], 'calendar': self.calendars.currentData(),
                'alias': ('teste' if for_events else 'principal') if guided else self.alias.text()}, self.selection_saved)

    def selection_saved(self, data):
        calendar = self.calendars.currentData()
        if 'candidate' in self.data:
            data = {**data, **{key: self.data[key] for key in
                ('candidate', 'matching_writer', 'calendar_notice')}, 'calendars': list(self.choices)}
        self.render(data)
        self.calendars.setCurrentIndex(self.calendars.findData(calendar))
        self.status.setText('Agenda salva. Marque "Usar a agenda salva nas consultas" se desejar e reinicie. '
                            'Criação e alteração de eventos não foram ativadas.')

    def connect_candidate(self):
        self.connect_operation('calendar_read')

    def connect_operation(self, operation):
        if operation not in {'calendar_read', 'calendar_write'}:
            return
        row = next((r for r in self.data.get('rows', []) if r['operation'] == operation and r['default']), None)
        if row:
            if row['status'] == 'conectado':
                self.refresh()
                return
            self.selected = row
            self.connect_account()

    def load_primary(self):
        reader = next((r for r in self.data.get('rows', [])
                       if r['operation'] == 'calendar_read' and r['default']), None)
        if reader:
            self.request('accounts.primary_calendar', {'connection': reader['id'],
                'revision': self.data['revision']}, self.render_calendars)

    def calendar_changed(self):
        item = next((r for r in self.choices if r['id'] == self.calendars.currentData()), None)
        self._calendar_timezone_valid = False
        if item and isinstance(item.get('timezone'), str) and item['timezone']:
            zone = QTimeZone(item['timezone'].encode())
            if zone.isValid():
                self._calendar_timezone_valid = True
                start, end = self.start.dateTime(), self.end.dateTime()
                for edit, value in ((self.start, start), (self.end, end)):
                    edit.setTimeZone(zone)
                    edit.setDateTime(value.toTimeZone(zone))
                self.timezone.setText(item['timezone'])
        if not self._calendar_timezone_valid:
            self.timezone.setText('Fuso não confirmado — escolha uma agenda ou atualize a consulta.')
        self.preview.setText('Seleção alterada. Revise novamente antes de solicitar confirmação.')
        self.after_request()

    def event_values(self):
        if not self.calendars.currentData():
            raise ValueError('Escolha uma agenda pelo nome real.')
        if not self._calendar_timezone_valid:
            raise ValueError('Não foi possível confirmar o fuso real da agenda. Atualize antes de revisar.')
        if not self.title.text().strip():
            raise ValueError('Informe o título do evento.')
        start, end = self.start.dateTime(), self.end.dateTime()
        if not start.isValid() or not end.isValid() or end <= start or start.secsTo(end) > 86400:
            raise ValueError('O fim deve ser posterior ao início, com duração máxima de 24 horas.')
        return {'calendario': 'teste', 'titulo': self.title.text(),
                'inicio': start.toString(Qt.DateFormat.ISODate),
                'fim': end.toString(Qt.DateFormat.ISODate), 'timezone': self.timezone.text()}

    def review(self):
        try:
            values = self.event_values()
            self.preview.setText(f"Agenda: {self.calendars.currentText()}\nConta: {self.account_summary.text()}\n"
                f"Título: {values['titulo']}\nInício: {self.start.text()}\nFim: {self.end.text()}\n"
                f"Timezone: {values['timezone']}\nDuração: {self.start.dateTime().secsTo(self.end.dateTime()) // 60} minutos.\n"
                'Sem convidados, recorrência, anexos ou lembretes. '
                'Esta revisão não cria o evento. Escrita exige confirmação vinculada pelo executor.')
            self.status.setText('Prévia pronta; nenhuma escrita realizada.')
        except ValueError as error:
            self.status.setText(str(error))

    def prepare(self):
        if self.busy or not self.data.get('write_enabled'):
            return
        writer = next((r for r in self.data['rows'] if r['operation'] == 'calendar_write' and r['default']), None)
        alias = 'teste' if 'candidate' in self.data else self.alias.text()
        if writer is None or alias not in writer['resources']:
            self.status.setText('Seleção WRITE padrão não corresponde ao alias; nenhuma solicitação enviada.')
            return
        if writer['resources'][alias] != self.calendars.currentData():
            self.status.setText('A agenda da prévia não corresponde à seleção salva; salve e revise novamente.')
            return
        action = self.action.currentData()
        values = {'calendario': alias}
        if action in {'criar', 'titulo'}:
            values['titulo'] = self.title.text()
        if action in {'criar', 'horario'}:
            try:
                dates = self.event_values()
            except ValueError as error:
                self.status.setText(str(error))
                return
            values.update(inicio=dates['inicio'], fim=dates['fim'], timezone=dates['timezone'])
        if action != 'criar':
            values['evento'] = self.event_id.text()
        self.submit_command('/agenda ' + ('criar' if action == 'criar' else 'alterar') + ' ' + json.dumps(values, ensure_ascii=False))
