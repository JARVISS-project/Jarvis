"""Conversation and installed voice settings over existing local administration."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                              QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from jarvis.ui.administration import AdministrativePage, literal_label


class ConversationPreferencesPage(AdministrativePage):
    def __init__(self, client, parent=None, *, identity_changed=None) -> None:
        super().__init__(client, parent)
        self.identity_changed = identity_changed
        outer = self.layout
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.layout.addWidget(literal_label("Conversa e voz local"))
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText("Nome de tratamento")
        self.tone = QComboBox()
        for label, value in (("Direto", "direto"), ("Equilibrado", "equilibrado"),
                             ("Descontraído", "descontraido")):
            self.tone.addItem(label, value)
        self.detail = QComboBox()
        self.detail.addItem("Respostas curtas", "curtas")
        self.detail.addItem("Respostas detalhadas", "detalhadas")
        self.opinions = QCheckBox("Oferecer opiniões fundamentadas quando solicitadas")
        self.spoken = QCheckBox("Habilitar resposta falada")
        self.voices = QComboBox()
        self.provider = QComboBox()
        for label, value in [('Automático · voz instalada', 'auto'), ('Piper · resposta rápida', 'piper'),
                             ('Voz personalizada · amostra local', 'chatterbox'), ('Voz do Windows', 'sapi')]:
            self.provider.addItem(label, value)
        self.provider_button = QPushButton('Aplicar mecanismo de voz')
        self.provider_button.clicked.connect(lambda: self.request('conversation.voice',
            {'provider': self.provider.currentData()}, self.loaded))
        self.import_button = QPushButton('Importar minha amostra MP3 ou WAV…')
        self.import_button.clicked.connect(self.import_sample)
        self.reference_status = literal_label('Nenhuma amostra pessoal importada.')
        self.pace = QComboBox()
        for label, value in (("Rápido", "fast"), ("Natural", "natural"), ("Calmo", "calm")):
            self.pace.addItem(label, value)
        for label, widget in (("Nome de tratamento", self.name), ("Tom", self.tone),
                              ("Detalhe", self.detail), ("Vozes Piper instaladas", self.voices),
                              ("Velocidade Piper", self.pace)):
            self.layout.addWidget(literal_label(label))
            self.layout.addWidget(widget)
        self.layout.addWidget(self.opinions)
        self.layout.addWidget(self.spoken)
        self.layout.addWidget(literal_label('Como o JARVIS deve falar'))
        self.layout.addWidget(self.provider)
        self.layout.addWidget(self.provider_button)
        self.layout.addWidget(self.import_button)
        self.layout.addWidget(self.reference_status)
        self.layout.addWidget(literal_label('Amostra com fala clara, de 3 a 30 segundos e até 10 MB. '
            'A voz personalizada requer o componente neural e seu modelo local; não há download automático. '
            'A amostra fica neste perfil e não acompanha o instalador público.'))
        self.layout.addWidget(literal_label("Salve antes de ouvir a prévia. Nenhuma voz é baixada ou reproduzida automaticamente. Preferências não alteram permissões ou memória."))
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Salvar preferências")
        self.refresh_button = QPushButton("Atualizar diagnóstico")
        self.preview_button = QPushButton("Ouvir prévia")
        self.stop_button = QPushButton("Interromper fala")
        self.reset_button = QPushButton("Restaurar tom e detalhe")
        for button in (self.save_button, self.refresh_button, self.preview_button):
            buttons.addWidget(button)
        self.layout.addLayout(buttons)
        secondary = QHBoxLayout()
        secondary.addWidget(self.stop_button)
        secondary.addWidget(self.reset_button)
        self.layout.addLayout(secondary)
        self.layout.addStretch()
        self.controls = [self.name, self.tone, self.detail, self.opinions, self.spoken,
                         self.voices, self.pace, self.save_button, self.refresh_button,
                         self.preview_button, self.reset_button, self.provider,
                         self.provider_button, self.import_button]
        self.data = {}
        self.save_button.clicked.connect(self.save)
        self.refresh_button.clicked.connect(self.refresh)
        self.preview_button.clicked.connect(self.preview)
        self.stop_button.clicked.connect(self.stop)
        self.reset_button.clicked.connect(self.reset)
        self.poll = QTimer(self)
        self.poll.setInterval(500)
        self.poll.timeout.connect(self.diagnose)

    def refresh(self) -> None:
        self.request("conversation.preferences", {}, self.loaded)

    def loaded(self, data: dict) -> None:
        self.data = data
        self.name.setText(data["user_display_name"])
        for combo, key in ((self.tone, "tone"), (self.detail, "detail"), (self.pace, "pace")):
            combo.setCurrentIndex(max(0, combo.findData(data[key])))
        self.opinions.setChecked(data["opinions"])
        self.spoken.setChecked(data["spoken"])
        self.provider.setCurrentIndex(max(0, self.provider.findData(data.get('provider', 'auto'))))
        self.reference_status.setText('Amostra pessoal importada neste perfil.' if data.get('reference')
                                     else 'Nenhuma amostra pessoal importada.')
        self.voices.clear()
        for voice in data["voices"]:
            self.voices.addItem(voice["label"], voice["id"])
        if not data["voice"]:
            self.voices.insertItem(0, "Voz configurada indisponível", "")
        self.voices.setCurrentIndex(max(0, self.voices.findData(data["voice"])))
        self.diagnosed(data)
        if callable(self.identity_changed):
            self.identity_changed(data["user_display_name"])

    def diagnosed(self, data: dict) -> None:
        self.data = data
        self.status.setText(data["diagnostic"])
        self.preview_button.setEnabled(data["available"] and not data["busy"])
        self.pace.setEnabled(data["speed_supported"])
        self.save_button.setEnabled(not data["busy"])
        self.provider_button.setEnabled(not data['busy'])
        self.import_button.setEnabled(not data['busy'])
        if data["busy"]:
            self.poll.start()
        else:
            self.poll.stop()

    def after_request(self) -> None:
        if self.data:
            self.preview_button.setEnabled(self.data["available"] and not self.data["busy"])
            self.pace.setEnabled(self.data["speed_supported"])
            self.save_button.setEnabled(not self.data["busy"])
            self.provider_button.setEnabled(not self.data['busy'])
            self.import_button.setEnabled(not self.data['busy'])

    def import_sample(self):
        source, _filter = QFileDialog.getOpenFileName(self, 'Escolha sua amostra de voz', '',
                                                    'Áudio de referência (*.mp3 *.wav)')
        if source:
            self.request('conversation.import_voice', {'source': source}, self.loaded)

    def diagnose(self) -> None:
        self.request("conversation.preferences", {}, self.diagnosed)

    def save(self) -> None:
        self.request("conversation.save", {
            "user_display_name": self.name.text(), "tone": self.tone.currentData(),
            "detail": self.detail.currentData(), "opinions": self.opinions.isChecked(),
            "spoken": self.spoken.isChecked(), "voice": self.voices.currentData() or "",
            "pace": self.pace.currentData()}, self.loaded)

    def preview(self) -> None:
        self.request("conversation.preview", {}, self.diagnosed)

    def stop(self) -> None:
        # Stop stays available while generation/synthesis is in progress.
        self.client.submit("conversation.stop", {}, lambda event: self.diagnosed(event["data"])
                           if event.get("status") == "done" else self.status.setText(event.get("error", "Saída indisponível.")))

    def reset(self) -> None:
        self.tone.setCurrentIndex(self.tone.findData("equilibrado"))
        self.detail.setCurrentIndex(self.detail.findData("curtas"))
        self.opinions.setChecked(True)
        self.status.setText("Tom e detalhe restaurados na prévia; salve para aplicar. Voz preservada.")
