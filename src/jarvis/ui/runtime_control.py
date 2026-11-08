"""Explicit local confirmation for stopping the connected runtime."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton, QVBoxLayout

from jarvis.ui.administration import AdministrativePage, literal_label


class RuntimeStopControl(AdministrativePage):
    def __init__(self, client, parent=None):
        super().__init__(client, parent)
        self.setStyleSheet('''
            QLabel { color: #c9dfe8; font-family: "Segoe UI"; font-size: 12px; }
            QDialog { background: #07141f; }
            QPushButton { color: #d9e8ee; background: #102a39;
                          border: 1px solid #315769; border-radius: 6px; padding: 6px; }
            QPushButton:disabled { color: #7b9ba8; }
        ''')
        self.setMaximumWidth(260)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.button = QPushButton('Encerrar JARVIS')
        self.button.clicked.connect(self.prepare)
        self.layout.insertWidget(0, self.button)
        self.controls = [self.button]
        self.confirmation = None
        self.generation = None
        self.requested = False
        self.button.setEnabled(False)
        self.status.setText('Disponível após conectar ao assistente.')

    def connected(self):
        if self.generation != self.client.generation:
            self.requested = False
            if self.confirmation is not None:
                self.confirmation.reject()
        self.generation = self.client.generation
        self.after_request()
        if not self.requested and not self.busy:
            self.status.setText('Encerra o assistente após sua confirmação.')

    def disconnected(self):
        if self.confirmation is not None:
            self.confirmation.reject()
        self.generation = None
        self.button.setEnabled(False)
        self.status.setText('Conexão encerrada; nenhuma solicitação será reenviada.')

    def after_request(self):
        self.button.setEnabled(bool(self.client.generation) and not self.busy
                               and not self.requested and self.confirmation is None)

    def prepare(self):
        if not self.button.isEnabled():
            return
        generation = self.client.generation

        def prepared(data):
            if generation != self.client.generation:
                return
            dialog = QDialog(self)
            dialog.setWindowTitle('Encerrar JARVIS')
            dialog.setModal(True)
            layout = QVBoxLayout(dialog)
            layout.addWidget(literal_label(data['preview']))
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Encerrar')
            cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
            cancel.setText('Continuar usando')
            cancel.setDefault(True)
            cancel.setFocus(Qt.FocusReason.OtherFocusReason)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.accepted.connect(lambda: self.finish(data['token'], generation, False))
            dialog.rejected.connect(lambda: self.finish(data['token'], generation, True))
            dialog.finished.connect(dialog.deleteLater)
            self.confirmation = dialog
            dialog.open()

        self.request('runtime.prepare_stop', {}, prepared)

    def finish(self, token, generation, cancel):
        self.confirmation = None
        if generation != self.client.generation or not self.client.generation:
            self.after_request()
            return

        def finished(data):
            self.requested = data.get('requested') is True
            self.status.setText('Encerramento solicitado. Aguarde a conclusão.' if self.requested
                                else 'Encerramento cancelado; continue usando o JARVIS.')

        self.request('runtime.cancel_stop' if cancel else 'runtime.stop', {'token': token}, finished)
