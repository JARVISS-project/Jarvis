"""Explicit API configuration and selected-text preview; no automatic sends."""
from PySide6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox

from jarvis.ui.administration import AdministrativePage, literal_label


class DelegationPage(AdministrativePage):
    def __init__(self, client, submit_command, parent=None):
        super().__init__(client, parent)
        self.submit_command = submit_command
        self.token = None
        self.data = {}
        self.layout.addWidget(literal_label('Delegação opcional — o assistente local continua padrão. '
            'Chave API e créditos são independentes de ChatGPT/Gemini e Google Calendar. '
            'Salvar e atualizar não testam conexão nem fazem envio pago. '
            'Autorize Modelos externos e Execução delegada em Permissões; cada envio exige confirmação.'))
        self.provider = QComboBox()
        self.provider.addItems(['openai', 'gemini'])
        self.model = QComboBox()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('Chave API; campo vazio preserva a chave existente. Nunca exibida ao carregar.')
        self.input_limit, self.output_limit, self.timeout = QSpinBox(), QSpinBox(), QSpinBox()
        for widget, low, high, value in [(self.input_limit, 64, 8192, 4096),
                (self.output_limit, 16, 4096, 512), (self.timeout, 5, 120, 30)]:
            widget.setRange(low, high)
            widget.setValue(value)
        for title, widget in [('Provedor', self.provider), ('Modelo', self.model), ('Chave API protegida', self.key),
                ('Entrada máxima (orçamento conservador de tokens)', self.input_limit),
                ('Saída máxima em tokens', self.output_limit), ('Timeout em segundos', self.timeout)]:
            self.layout.addWidget(literal_label(title))
            self.layout.addWidget(widget)
        self.availability = literal_label('Atualize para conferir a configuração local.')
        self.layout.addWidget(self.availability)
        self.refresh_button = QPushButton('Atualizar disponibilidade local — sem envio')
        self.refresh_button.clicked.connect(self.refresh)
        self.save_button = QPushButton('Salvar configuração API — sem envio')
        self.save_button.clicked.connect(self.save)
        self.task, self.context = QPlainTextEdit(), QPlainTextEdit()
        self.task.setPlaceholderText('Tarefa exata selecionada para enviar')
        self.context.setPlaceholderText('Opcional: texto que você selecionou e deseja compartilhar. '
            'Não incluímos histórico, memória, arquivos ou agenda automaticamente. Não cole segredos.')
        self.preview = literal_label('Prepare para revisar os dados exatos. Nenhum envio ocorreu.')
        self.prepare_button = QPushButton('Preparar prévia — sem envio')
        self.prepare_button.clicked.connect(self.prepare)
        self.execute_button = QPushButton('Solicitar confirmação na Conversa')
        self.execute_button.setEnabled(False)
        self.execute_button.clicked.connect(self.execute)
        self.cancel_button = QPushButton('Cancelar delegação')
        self.cancel_button.clicked.connect(self.cancel)
        for widget in (self.refresh_button, self.save_button, self.task, self.context, self.prepare_button,
                       self.preview, self.execute_button, self.cancel_button):
            self.layout.addWidget(widget)
        self.controls = [self.save_button, self.prepare_button, self.refresh_button]
        self.provider.currentTextChanged.connect(self.select)
        for signal in (self.task.textChanged, self.context.textChanged, self.model.currentTextChanged,
                       self.key.textChanged, self.input_limit.valueChanged, self.output_limit.valueChanged,
                       self.timeout.valueChanged):
            signal.connect(self.edited)
        self.layout.addWidget(literal_label('Cancelar após envio pode não impedir cobrança. '
            'Resposta externa é apenas texto; não executa instruções. '
            'Resultado e progresso aparecem na Conversa; atualizar mostra os estados sem conteúdo privado.'))

    def edited(self, *_):
        had_token = self.token is not None
        self.token = None
        self.execute_button.setEnabled(False)
        if had_token:
            self.client.submit('delegation.cancel', {}, lambda event: None)

    def refresh(self):
        self.request('delegation.list', {}, self.render)

    def render(self, data):
        self.data = data
        self.select()
        labels = {'sending': 'enviando', 'responding': 'respondendo', 'completed': 'concluído',
                  'cancelled': 'cancelado', 'failed': 'falhou'}
        self.status.setText('Resultados: ' + ', '.join(labels.get(r['state'], 'indisponível')
                                                    for r in data.get('history', [])))

    def select(self, *_):
        self.edited()
        row = next((r for r in self.data.get('providers', []) if r['provider'] == self.provider.currentText()), {})
        self.model.clear()
        self.model.addItems(row.get('models', []))
        if row.get('model'):
            self.model.setCurrentText(row['model'])
        self.input_limit.setValue(row.get('input_limit', 4096))
        self.output_limit.setValue(row.get('output_limit', 512))
        self.timeout.setValue(row.get('timeout', 30))
        self.key.clear()
        self.availability.setText(row.get('status', 'Atualize para listar modelos disponíveis.'))

    def save(self):
        arguments = {'provider': self.provider.currentText(), 'model': self.model.currentText(),
                     'input_limit': self.input_limit.value(), 'output_limit': self.output_limit.value(),
                     'timeout': self.timeout.value(), 'key': self.key.text()}
        self.key.clear()
        self.request('delegation.configure', arguments, self.render)

    def prepare(self):
        def prepared(data):
            self.token = data['token']
            self.preview.setText(data['text'])
            self.execute_button.setEnabled(True)
        self.request('delegation.prepare', {'provider': self.provider.currentText(),
                     'task': self.task.toPlainText(), 'context': self.context.toPlainText()}, prepared)

    def execute(self):
        if self.token:
            token = self.token
            self.execute_button.setEnabled(False)
            self.submit_command('/delegacao executar ' + token)

    def cancel(self):
        self.token = None
        self.execute_button.setEnabled(False)
        self.client.submit('delegation.cancel', {}, lambda event: self.status.setText(
            'Cancelado; envio já recebido pode ser cobrado.'))
