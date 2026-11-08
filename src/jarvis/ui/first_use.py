"""First use, searchable help and explicit local profile previews."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from jarvis.ui.administration import AdministrativePage, literal_label


class AvailabilityPanel(AdministrativePage):
    """The existing home dashboard consumes the same read-only help inventory."""

    def __init__(self, client, navigate, parent=None) -> None:
        super().__init__(client, parent)
        self.navigate = navigate
        self.data = {}
        self.setStyleSheet('''
            QLabel { color: #c9dfe8; font-family: "Segoe UI"; font-size: 12px; }
            QPushButton { color: #d9e8ee; background: #102a39; border: 1px solid #315769;
                          border-radius: 6px; padding: 6px; }
            QPushButton:disabled { color: #7b9ba8; }
            QScrollArea { background: #07141f; border: 1px solid #234353; border-radius: 8px; }
        ''')
        self.layout.addWidget(literal_label('Disponibilidade — configuração local, sem testes automáticos'))
        self.refresh_button = QPushButton('Atualizar painel — sem executar ações')
        self.refresh_button.clicked.connect(self.refresh)
        self.layout.addWidget(self.refresh_button)
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.contents = QWidget()
        self.contents.setStyleSheet('background: #07141f;')
        self.rows = QVBoxLayout(self.contents)
        scroll.setWidget(self.contents)
        self.layout.addWidget(scroll, 1)
        self.controls = [self.refresh_button]
        self.status.setText('Runtime não conectado. Abra o JARVIS para consultar o estado local.')

    def refresh(self) -> None:
        self.request('help.list', {}, self.loaded)

    def disconnected(self) -> None:
        self.clear_rows()
        self.data = {}
        self.status.setText('Runtime desconectado. Reabra o JARVIS; nenhuma ação será reenviada.')

    def clear_rows(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def loaded(self, data: dict) -> None:
        self.data = data
        self.clear_rows()
        for row in data.get('availability', []):
            self.rows.addWidget(literal_label(f"{row['name']} — {row['status']}\n{row['reason']}\n{row['next_action']}"))
            button = QPushButton('Abrir ' + row['page'])
            button.clicked.connect(lambda _=False, page=row['page']: self.navigate(page))
            self.rows.addWidget(button)
        self.rows.addStretch(1)
        self.status.setText('Estado local atualizado. Credencial não comprova serviço disponível. '
                            'Tutorial e diagnóstico em Ajuda.')


class FirstUsePage(AdministrativePage):
    def __init__(self, client, navigate, parent=None) -> None:
        super().__init__(client, parent)
        self.setStyleSheet('QLabel { color: #c9dfe8; font-family: "Segoe UI"; font-size: 12px; }')
        self.navigate = navigate
        self.data = {}
        self.confirmation = None
        self._startup_checked = False
        self.title = literal_label('Primeiro uso e Ajuda')
        self.instructions = literal_label('O tutorial não concede acessos nem ativa recursos.')
        self.layout.addWidget(self.title)
        self.layout.addWidget(self.instructions)
        buttons = QHBoxLayout()
        self.back = QPushButton('Voltar')
        self.next = QPushButton('Próximo')
        self.skip = QPushButton('Pular por enquanto')
        self.resume = QPushButton('Retomar tutorial')
        self.open_page = QPushButton('Abrir configuração desta etapa')
        for button, action in ((self.back, 'back'), (self.next, 'next'), (self.skip, 'skip'), (self.resume, 'resume')):
            button.clicked.connect(lambda _=False, a=action: self.progress(a))
            buttons.addWidget(button)
        self.open_page.clicked.connect(self.open_step)
        buttons.addWidget(self.open_page)
        self.layout.addLayout(buttons)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar exemplos: agenda, documentos, voz…')
        self.search.textChanged.connect(self.filter_examples)
        self.layout.addWidget(self.search)
        self.examples = QListWidget()
        self.examples.currentItemChanged.connect(self.show_example)
        self.layout.addWidget(self.examples, 1)
        self.example_text = QPlainTextEdit()
        self.example_text.setReadOnly(True)
        self.example_text.setMaximumHeight(95)
        self.layout.addWidget(self.example_text)
        self.example_settings = QPushButton('Conferir configuração do exemplo')
        self.example_settings.clicked.connect(self.open_example)
        self.layout.addWidget(self.example_settings)
        diagnostic_buttons = QHBoxLayout()
        self.diagnose_button = QPushButton('Diagnosticar computador (somente leitura)')
        self.diagnose_button.clicked.connect(self.diagnose)
        self.profiles = QComboBox()
        self.prepare_button = QPushButton('Ver prévia do perfil STT')
        self.prepare_button.clicked.connect(self.prepare)
        self.rollback_button = QPushButton('Restaurar perfil anterior')
        self.rollback_button.clicked.connect(self.rollback)
        self.rollback_button.setEnabled(False)
        for control in (self.diagnose_button, self.profiles, self.prepare_button, self.rollback_button):
            diagnostic_buttons.addWidget(control)
        self.layout.addLayout(diagnostic_buttons)
        self.diagnostic = QPlainTextEdit()
        self.diagnostic.setReadOnly(True)
        self.diagnostic.setMaximumHeight(180)
        self.layout.addWidget(self.diagnostic)
        self.advanced_button = QPushButton('Detalhes avançados')
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self.show_advanced)
        self.layout.addWidget(self.advanced_button)
        self.controls = [self.back, self.next, self.skip, self.resume, self.open_page,
                         self.diagnose_button, self.prepare_button, self.profiles, self.rollback_button]
        self._rollback_available = False

    def check_startup(self) -> None:
        if self._startup_checked or not self.client.generation:
            return
        self._startup_checked = True
        def loaded(data):
            self.loaded(data)
            if data['progress']['auto_show'] and self.window().page_stack.currentIndex() == 0:
                self.navigate('Ajuda')
        self.request('help.list', {}, loaded)

    def refresh(self) -> None:
        self.request('help.list', {}, self.loaded)

    def loaded(self, data: dict) -> None:
        self.data = data
        progress = data['progress']
        step = next(s for s in data['steps'] if s['id'] == progress['step'])
        index = data['steps'].index(step)
        self.title.setText(f"{index + 1}/6 — {step['title']}")
        self.instructions.setText(step['text'])
        self.status.setText('Tutorial concluído. Ajuda continua disponível.' if progress['finished'] else
                            'Tutorial pausado; retome quando quiser.' if progress['dismissed'] else
                            'Tutorial disponível. Nada será ativado automaticamente.' if progress['valid'] else
                            'Progresso inválido; detalhes avançados ajudam no diagnóstico. Configuração preservada.')
        self.filter_examples()
        if data.get('diagnostic'):
            self.show_diagnostic(data['diagnostic'])
        self.after_request()

    def progress(self, action: str) -> None:
        def updated(progress):
            self.data['progress'] = progress
            self.loaded(self.data)
        self.request('help.progress', {'action': action}, updated)

    def after_request(self) -> None:
        if self.busy:
            return
        state = self.data.get('progress', {})
        self.back.setEnabled(state.get('valid', False) and state.get('step') != 'welcome')
        self.next.setEnabled(state.get('valid', False) and not state.get('finished', False))
        self.skip.setEnabled(state.get('valid', False))
        self.resume.setEnabled(state.get('valid', False))
        self.open_page.setEnabled(bool(self.current_step().get('page')))
        self.prepare_button.setEnabled(self.profiles.count() > 0)
        self.rollback_button.setEnabled(self._rollback_available)

    def current_step(self) -> dict:
        return next((s for s in self.data.get('steps', []) if s['id'] == self.data.get('progress', {}).get('step')), {})

    def open_step(self) -> None:
        page = self.current_step().get('page')
        if page:
            self.navigate(page)

    def filter_examples(self) -> None:
        self.examples.clear()
        for row in self.data.get('examples', []):
            if self.search.text().casefold() not in (row['category'] + ' ' + row['example']).casefold():
                continue
            item = QListWidgetItem(f"{row['category']} — {row['status']}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.examples.addItem(item)
        if self.examples.count():
            self.examples.setCurrentRow(0)
        else:
            self.example_text.clear()
            self.example_settings.setEnabled(False)

    def show_example(self, item, previous=None) -> None:
        self.example_settings.setEnabled(item is not None)
        if item:
            row = item.data(Qt.ItemDataRole.UserRole)
            self.example_text.setPlainText(row['example'] + '\n' + row['next_action'] + '\nConsultar este exemplo não executa nada.')

    def open_example(self) -> None:
        item = self.examples.currentItem()
        if item:
            self.navigate(item.data(Qt.ItemDataRole.UserRole)['page'])

    def diagnose(self) -> None:
        def loaded(data):
            self.data['diagnostic'] = data
            self.show_diagnostic(data)
            self.status.setText('Leitura concluída. Perfil preservado; inferência não testada.')
            # Refresh examples using the newly measured assistant availability.
            QTimer.singleShot(0, self.refresh)
        self.request('help.diagnose', {}, loaded)

    @staticmethod
    def gib(value) -> str:
        return f'{value / 1024**3:.2f} GiB' if isinstance(value, int) else 'indisponível'

    def show_diagnostic(self, data) -> None:
        ram, disk, vram = data['ram'], data['disk'], data['vram']
        system = data['system']
        lines = [f"{system['os']} / {system['architecture']} / {system['cpu']}",
                 f"RAM total: {self.gib(ram['total_bytes'])}; disponível agora: {self.gib(ram['available_bytes'])}",
                 f"VRAM total: {vram['total_mib']} MiB; livre agora: {vram['free_mib']} MiB ({vram['status']})",
                 f"Disco livre: {self.gib(disk['free_bytes'])}",
                 f"Assistente: {data['assistant']['status']}. {data['assistant']['next_action']}",
                 'Modelos instalados: ' + (', '.join(m['name'] for m in data['ollama']['models']) or 'nenhum detectado'),
                 *data['warnings']]
        if data.get('voice'):
            lines.append('Voz: ' + data['voice']['diagnostic'])
        lines.extend(f"{p['profile']}: {p['status']}. {p['reason']}" for p in data['profiles'])
        self.diagnostic.setPlainText('\n'.join(lines))
        self.profiles.clear()
        for profile in data['profiles']:
            self.profiles.addItem(profile['profile'], profile['profile'])
        self.profiles.setCurrentIndex(self.profiles.findData(data['current_profile']))
        self.after_request()

    def show_advanced(self, checked) -> None:
        if checked:
            self.diagnostic.setPlainText(self.data.get('advanced', '') + '\n' + json.dumps(self.data.get('diagnostic'), ensure_ascii=False, indent=2))
        elif self.data.get('diagnostic'):
            self.show_diagnostic(self.data['diagnostic'])
        else:
            self.diagnostic.clear()

    def prepare(self) -> None:
        def prepared(data):
            dialog = QDialog(self)
            dialog.setWindowTitle('Confirmar perfil local')
            from PySide6.QtWidgets import QVBoxLayout
            layout = QVBoxLayout(dialog)
            layout.addWidget(literal_label(f"Atual: {data['current']}\nProposto: {data['proposed']}\n{data['detail']}"))
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
            layout.addWidget(buttons)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            def finished(result):
                self.confirmation = None
                if result == QDialog.DialogCode.Accepted:
                    self.request('help.profile_apply', {'token': data['token']}, self.profile_applied)
                dialog.deleteLater()
            dialog.finished.connect(finished)
            self.confirmation = dialog
            dialog.open()
        self.request('help.profile_prepare', {'profile': self.profiles.currentData()}, prepared)

    def profile_applied(self, data) -> None:
        self._rollback_available = data['rollback_available']
        self.status.setText(f"Perfil salvo: {data['profile']}. Reinicie para ativar. Nenhum modelo foi carregado.")
        self.after_request()

    def rollback(self) -> None:
        self.request('help.profile_rollback', {}, self.profile_applied)
