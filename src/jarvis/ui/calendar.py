"""Read-only agenda table. No providers, account grants or persisted events."""
from __future__ import annotations

from datetime import datetime, timedelta
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFrame, QHBoxLayout,
    QHeaderView, QLineEdit, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
    QSplitter, QScrollArea,
)

from jarvis.ui.administration import AdministrativePage, literal_label


PERIODS = (('today', 'Hoje'), ('tomorrow', 'Amanhã'), ('upcoming', 'Próximos 7 dias'))


class CalendarPage(AdministrativePage):
    def __init__(self, client, navigate, parent=None):
        super().__init__(client, parent)
        self.setObjectName('calendarPage')
        self.setStyleSheet('''
            QWidget#calendarPage { background: #0a1520; }
            QLabel#pageTitle { font-size: 30px; font-weight: 600; color: #edf4fa; }
            QLabel#muted { color: #9bafc0; }
            QFrame#calendarMetric, QFrame#calendarEmpty { background: #111f2d;
                border: 1px solid #293c4e; border-radius: 12px; }
            QLabel#metricValue { font-size: 22px; font-weight: 600; color: #c1f2eb; }
            QLabel#emptyTitle { font-size: 20px; font-weight: 600; }
            QTableWidget { background: #101e2b; alternate-background-color: #132331;
                border: 1px solid #293c4e; border-radius: 10px; gridline-color: #26394a;
                selection-background-color: #214456; selection-color: #e9fbfa; }
            QTableWidget::item { padding: 10px; border-bottom: 1px solid #243546; }
            QHeaderView::section { background: #182a39; color: #a8becf;
                padding: 12px 10px; border: 0; border-bottom: 1px solid #30495c;
                font-size: 12px; font-weight: 600; }
        ''')
        self.layout.setContentsMargins(30, 26, 30, 24)
        self.layout.setSpacing(12)
        self.presentation_changed = None
        self.layout.removeWidget(self.status)
        self.status.setObjectName('muted')
        header = QHBoxLayout()
        copy = QVBoxLayout()
        title = literal_label('Agenda')
        title.setObjectName('pageTitle')
        copy.addWidget(title)
        subtitle = literal_label('Seus compromissos, em um só lugar.')
        subtitle.setObjectName('muted')
        copy.addWidget(subtitle)
        header.addLayout(copy, 1)
        self.setup_button = QPushButton('Configurar conta')
        self.setup_button.clicked.connect(lambda: navigate('Contas e conexões'))
        header.addWidget(self.setup_button)
        self.contacts_button = QPushButton('Meus contatos')
        self.contacts_button.clicked.connect(self.manage_contacts)
        header.addWidget(self.contacts_button)
        self.layout.addLayout(header)

        metrics = QHBoxLayout()
        self.period_value = self._metric(metrics, 'PERÍODO', 'Hoje')
        self.count_value = self._metric(metrics, 'COMPROMISSOS', '—')
        self.days_value = self._metric(metrics, 'DIA INTEIRO', '—')
        self.layout.addLayout(metrics)

        toolbar = QHBoxLayout()
        self.period = 'today'
        self.period_buttons = {}
        self.period_group = QButtonGroup(self)
        for key, label in PERIODS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == self.period)
            button.clicked.connect(lambda _checked=False, value=key: self.change_period(value))
            self.period_group.addButton(button)
            self.period_buttons[key] = button
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.calendars = QComboBox()
        self.calendars.setAccessibleName('Agenda para consulta')
        self.calendars.setPlaceholderText('Escolha uma agenda')
        self.calendars.setMinimumContentsLength(10)
        self.calendars.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.calendars.currentIndexChanged.connect(self.calendar_changed)
        toolbar.addWidget(self.calendars)
        self.refresh_button = QPushButton('Carregar eventos')
        self.refresh_button.setObjectName('primaryAction')
        self.refresh_button.clicked.connect(self.load_events)
        toolbar.addWidget(self.refresh_button)
        self.layout.addLayout(toolbar)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar nos eventos carregados…')
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('Buscar compromisso ou local')
        self.search.textChanged.connect(self.filter_rows)
        search_row.addWidget(self.search, 1)
        self.result_count = literal_label('Até 20 eventos por consulta')
        self.result_count.setObjectName('muted')
        search_row.addWidget(self.result_count)
        self.view_mode = QComboBox()
        self.view_mode.addItem('Agenda por horários', 'schedule')
        self.view_mode.addItem('Tabela completa', 'table')
        search_row.addWidget(self.view_mode)
        self.suggestions_button = QPushButton('Sugestões para o compromisso')
        self.suggestions_button.clicked.connect(self.open_assistant)
        search_row.addWidget(self.suggestions_button)
        self.layout.addLayout(search_row)

        self.body = QStackedWidget()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(('Data', 'Horário', 'Compromisso', 'Local'))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setAccessibleName('Compromissos da agenda')
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(3, 155)
        self.table.currentCellChanged.connect(self.show_details)
        self.body.addWidget(self.table)
        self.empty = QFrame()
        self.empty.setObjectName('calendarEmpty')
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(40, 28, 40, 28)
        empty_layout.addStretch()
        self.empty_title = literal_label('Sua agenda começa aqui')
        self.empty_title.setObjectName('emptyTitle')
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message = literal_label('Carregue os eventos para consultar seus compromissos.')
        self.empty_message.setObjectName('muted')
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_message)
        empty_layout.addStretch()
        self.body.addWidget(self.empty)
        self.body.setCurrentWidget(self.empty)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.body)
        self.side_stack = QStackedWidget()
        self.side_stack.setMinimumWidth(270)
        self.detail_panel = QFrame()
        self.detail_panel.setObjectName('detailPanel')
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        self.detail_title = literal_label('Selecione um compromisso')
        self.detail_title.setObjectName('cardPrimary')
        self.detail_when, self.detail_where, self.detail_people, self.detail_notes = [literal_label('') for _ in range(4)]
        for label in (self.detail_title, self.detail_when, self.detail_where, self.detail_people, self.detail_notes):
            detail_layout.addWidget(label)
        self.detail_action = QPushButton('Sugestões e preparação')
        self.detail_action.setObjectName('primaryAction')
        self.detail_action.clicked.connect(self.open_assistant)
        detail_layout.addWidget(self.detail_action)
        detail_layout.addStretch(1)
        self.detail_surface = QScrollArea()
        self.detail_surface.setWidgetResizable(True)
        self.detail_surface.setWidget(self.detail_panel)
        self.side_stack.addWidget(self.detail_surface)
        self.splitter.addWidget(self.side_stack)
        self.splitter.setSizes([580, 300])
        self.layout.addWidget(self.splitter, 1)
        self.details = literal_label('Selecione um compromisso para ver seus detalhes.')
        self.details.setObjectName('muted')
        self.layout.addWidget(self.details)
        self.layout.addWidget(self.status)
        self.controls = [self.calendars, self.refresh_button, *self.period_buttons.values()]
        self.controls.append(self.suggestions_button)
        self.assistant_window = None
        self.contacts_window = None
        self.rows = []
        self.suggestions_button.setEnabled(False)
        self._ready = False
        self._loaded = False
        self._limit_reached = False
        self.view_mode.currentIndexChanged.connect(self.apply_presentation)
        self.apply_presentation()
        self.after_request()

    def _metric(self, layout, title, value):
        card = QFrame()
        card.setObjectName('calendarMetric')
        column = QVBoxLayout(card)
        column.setContentsMargins(14, 8, 14, 8)
        caption = literal_label(title)
        caption.setObjectName('muted')
        result = literal_label(value)
        result.setObjectName('metricValue')
        column.addWidget(caption)
        column.addWidget(result)
        layout.addWidget(card, 1)
        return result

    def refresh(self):
        # Opening the page inspects local readiness; Google is queried on demand.
        if self.busy:
            return
        self.clear_events()
        self.request('calendar.state', {}, self.render_state)

    def render_state(self, data):
        previous = self.calendars.currentData()
        self.calendars.blockSignals(True)
        self.calendars.clear()
        for alias in data.get('calendars', []):
            self.calendars.addItem(alias, alias)
        index = self.calendars.findData(previous or 'principal')
        self.calendars.setCurrentIndex(index if index >= 0 else (0 if self.calendars.count() == 1 else -1))
        self.calendars.blockSignals(False)
        self._ready = data['state'] == 'ready'
        self.status.setText(data['message'])
        self.show_empty('Pronta para consultar' if self._ready else 'Configure sua agenda', data['message'])

    def change_period(self, period):
        was_loaded = self._loaded
        self.period = period
        self.period_value.setText(dict(PERIODS)[period])
        self.clear_events()
        if was_loaded:
            self.load_events()
        self.apply_presentation()

    def calendar_changed(self):
        self.clear_events()
        self.after_request()

    def clear_events(self):
        self.rows = []
        self.table.setRowCount(0)
        self._loaded = False
        self._limit_reached = False
        self.count_value.setText('—')
        self.days_value.setText('—')
        self.search.clear()
        self.details.setText('Os eventos ficam somente em memória nesta tela.')
        self.refresh_button.setText('Carregar eventos')
        self.result_count.setText('Até 20 eventos por consulta')
        self.show_empty('Carregue sua agenda', 'Escolha o período e clique em Carregar eventos.')
        self.detail_title.setText('Selecione um compromisso')
        for label in (self.detail_when, self.detail_where, self.detail_people, self.detail_notes):
            label.clear()
        self.detail_action.setEnabled(False)

    def load_events(self):
        if self.busy or not self._ready or self.calendars.currentData() is None:
            return
        self.clear_events()
        self.show_empty('Consultando agenda…', 'Buscando os compromissos do período escolhido.')
        self.request('calendar.events', {'alias': self.calendars.currentData(), 'period': self.period}, self.render_events)

    def render_events(self, data):
        if data['state'] != 'loaded':
            self.status.setText(data['message'])
            self.show_empty('Agenda indisponível', data['message'])
            if 'calendars' in data:
                self._ready = False
            return
        self.rows = data['rows']
        self._loaded = True
        self._limit_reached = data['limit_reached']
        self.count_value.setText(str(len(self.rows)) + ('+' if self._limit_reached else ''))
        self.days_value.setText(str(sum(row['all_day'] for row in self.rows)))
        self.refresh_button.setText('Atualizar eventos')
        self.status.setText('Limite de 20 eventos atingido; pode haver outros compromissos.' if self._limit_reached else
                            f"Atualizada às {datetime.now():%H:%M} · Fuso: {data['timezone']} · Somente leitura")
        self.filter_rows()

    def filter_rows(self):
        if not hasattr(self, 'rows'):
            return
        query = self.search.text().strip().casefold()
        self.table.setCurrentCell(-1, -1)
        filtered = [row for row in self.rows if query in (row['title'] + ' ' + row['location']).casefold()]
        self.table.setRowCount(len(filtered))
        for index, row in enumerate(filtered):
            start, end = datetime.fromisoformat(row['start']), datetime.fromisoformat(row['end'])
            date = start.strftime('%d/%m/%Y')
            if row['all_day']:
                last = (end - timedelta(days=1)).date()
                if last > start.date():
                    date = f'{start:%d/%m} – {last:%d/%m}'
                hours = 'Dia inteiro'
            else:
                hours = f'{start:%H:%M} – {end:%H:%M}' if start.date() == end.date() else f'{start:%H:%M} – {end:%d/%m %H:%M}'
            for column, value in enumerate((date, hours, row['title'], row['location'] or '—')):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row)
                # Qt tooltips autodetect HTML even though table cells are plain.
                item.setToolTip('<qt>' + html.escape(value) + '</qt>')
                self.table.setItem(index, column, item)
        self.result_count.setText(f'{len(filtered)} de {len(self.rows)} eventos' + (' · limite atingido' if self._limit_reached else ''))
        self.details.setText('Selecione um compromisso para ver seus detalhes.')
        if filtered:
            self.body.setCurrentWidget(self.table)
        elif query:
            self.show_empty('Nenhum resultado', 'Tente outro título ou local na busca.')
        elif self._loaded:
            self.show_empty('Nenhum compromisso neste período', 'Sua consulta foi concluída. Você pode escolher outro período.')

    def show_details(self, row, _column, *_previous):
        title, location = self.table.item(row, 2), self.table.item(row, 3)
        if title is not None and location is not None:
            self.details.setText(f'{title.text()} · {location.text()}')
            event = title.data(Qt.ItemDataRole.UserRole)
            if event:
                start, end = datetime.fromisoformat(event['start']), datetime.fromisoformat(event['end'])
                self.detail_title.setText(event['title'])
                self.detail_when.setText(f'{start:%d/%m/%Y} · Dia inteiro' if event['all_day'] else f'{start:%d/%m/%Y %H:%M} – {end:%H:%M}')
                self.detail_where.setText(event['location'] or 'Local ou link não informado.')
                people = [person['name'] or person['email'] for person in event.get('participants', []) if not person['self'] and not person['resource']]
                self.detail_people.setText('Participantes: ' + ', '.join(people) if people else 'Nenhum participante identificado no evento.')
                self.detail_notes.setText(event.get('notes', ''))
        self.suggestions_button.setEnabled(not self.busy and title is not None)
        self.detail_action.setEnabled(not self.busy and title is not None)

    def open_assistant(self):
        item = self.table.item(self.table.currentRow(), 2)
        if item is not None and not self.busy:
            from jarvis.ui.calendar_assistant import CalendarAssistantPage
            from jarvis.ui.workbench import InlineSurface
            if self.assistant_window:
                self.assistant_window.reject()
            page = CalendarAssistantPage(self.client, self.calendars.currentData(), self.period, item.data(Qt.ItemDataRole.UserRole), self)
            def restore():
                self.side_stack.setCurrentWidget(self.detail_surface)
                self.assistant_window = None
            self.assistant_window = InlineSurface(page, restore, self)
            self.side_stack.addWidget(self.assistant_window)
            self.side_stack.setCurrentWidget(self.assistant_window)
            page.refresh()

    def apply_presentation(self, *_args):
        schedule = self.view_mode.currentData() == 'schedule'
        self.table.setColumnHidden(0, schedule and self.period != 'upcoming')
        self.table.setColumnHidden(3, schedule)
        self.table.horizontalHeader().setVisible(not schedule)
        self.table.setAlternatingRowColors(not schedule)
        self.table.verticalHeader().setDefaultSectionSize(72 if schedule else 54)
        if callable(self.presentation_changed):
            self.presentation_changed()

    def manage_contacts(self):
        from jarvis.ui.calendar_assistant import contacts_dialog
        self.contacts_window = contacts_dialog(self.client, self)
        self.contacts_window.open()

    def show_empty(self, title, message):
        self.empty_title.setText(title)
        self.empty_message.setText(message)
        self.body.setCurrentWidget(self.empty)

    def after_request(self):
        if not hasattr(self, '_ready'):
            return
        self.refresh_button.setEnabled(not self.busy and self._ready and self.calendars.currentData() is not None)
        self.suggestions_button.setEnabled(not self.busy and self.table.currentRow() >= 0 and bool(self.rows))
        if not self.busy and not self._loaded and self.empty_title.text() == 'Consultando agenda…':
            self.show_empty('Não foi possível carregar', self.status.text())

    def connection_event(self, event):
        if event.get('status') in {'disconnected', 'ready'}:
            for window in (self.assistant_window, self.contacts_window):
                if window is not None:
                    window.reject()
            self._ready = False
            self.clear_events()
            self.status.setText('Conexão alterada. Abra a Agenda novamente para atualizar.')
            self.after_request()
