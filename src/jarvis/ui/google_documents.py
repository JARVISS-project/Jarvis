"""Explicit UI entry; no network/browser work on dialog construction."""
from __future__ import annotations

import os
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QLabel, QPushButton, QVBoxLayout

from jarvis.config import JarvisConfig
from jarvis.integrations.google.drive_docs.picker import GoogleDocumentPicker


class _PickerWorker(QThread):
    finished_message = Signal(str)

    def __init__(self, picker, path, parent):
        super().__init__(parent)
        self.picker, self.path = picker, path
        self.cancel = Event()

    def run(self):
        try:
            message = self.picker.select(self.path, cancel=self.cancel)
        except Exception:
            message = 'Seleção não concluída; nenhuma leitura confirmada. Revise configuração/consentimento.'
        self.finished_message.emit(message)


class GoogleDocumentsDialog(QDialog):
    def __init__(self, parent=None, *, picker=None, config=None):
        super().__init__(parent)
        self.setWindowTitle('Documentos Google — autorizações locais')
        self.config = config or JarvisConfig()
        self.picker = picker or GoogleDocumentPicker(self.config)
        self.worker = None
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        layout = QVBoxLayout(self)
        self.status = QLabel('Ação explícita abre consentimento/Picker oficial. Selecione só Google Docs de teste.\n'
                             'Seleção não habilita READ. Remover/desconectar é local, não revoga a conta Google.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.select_button = QPushButton('Selecionar documentos Google')
        self.select_button.clicked.connect(self.select_documents)
        layout.addWidget(self.select_button)
        self.aliases = QComboBox()
        layout.addWidget(self.aliases)
        self.remove_button = QPushButton('Remover autorização local do arquivo')
        self.remove_button.clicked.connect(self.remove_document)
        layout.addWidget(self.remove_button)
        self.disconnect_button = QPushButton('Desconectar Drive/Docs localmente')
        self.disconnect_button.clicked.connect(self.disconnect)
        layout.addWidget(self.disconnect_button)
        self.cancel_button = QPushButton('Cancelar seleção / fechar')
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        self.refresh_aliases()

    def refresh_aliases(self):
        self.aliases.clear()
        try:
            self.aliases.addItems(sorted(self.picker.local.read().get('google_drive_docs_files', {})))
        except Exception:
            self.status.setText('Configuração local indisponível; nenhuma alteração efetuada.')

    def select_documents(self):
        if self.worker is not None and self.worker.isRunning():
            return
        try:
            configured = (os.getenv('JARVIS_GOOGLE_DRIVE_DOCS_CLIENT_CONFIG')
                          or self.picker.local.read().get('google_drive_docs_client_config'))
        except Exception:
            self.status.setText('Settings inválido; nenhuma seleção iniciada.')
            return
        if configured:
            path = Path(configured)
        else:
            selected, _filter = QFileDialog.getOpenFileName(self, 'JSON OAuth Desktop (somente caminho)',
                                                          '', 'JSON (*.json)')
            if not selected:
                return
            path = Path(selected)
        self.status.setText('No navegador: entre na conta, consinta e selecione Google Docs sem informação sensível.')
        self.worker = _PickerWorker(self.picker, path, self)
        self.worker.finished_message.connect(self._show_result)
        self.worker.finished.connect(self._worker_stopped)
        for button in (self.select_button, self.remove_button, self.disconnect_button):
            button.setEnabled(False)
        self.worker.start()

    def _show_result(self, message):
        self.status.setText(message)
        self.refresh_aliases()

    def _worker_stopped(self):
        for button in (self.select_button, self.remove_button, self.disconnect_button):
            button.setEnabled(True)

    def remove_document(self):
        try:
            self.picker.local.remove(self.aliases.currentText())
            self.status.setText('Autorização removida; READ desligado. Runtime antigo bloqueia até reiniciar.')
            self.refresh_aliases()
        except Exception:
            self.status.setText('Remoção local não concluída; nenhum payload privado exibido.')

    def disconnect(self):
        try:
            self.picker.disconnect_local()
            self.status.setText('Drive/Docs desconectado localmente; Calendar e concessão Google preservados.')
            self.refresh_aliases()
        except Exception:
            self.status.setText('Desconexão local não concluída; verifique configuração/armazenamento.')

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel.set()
            self.status.setText('Cancelando; aguarde o encerramento do callback antes de fechar.')
            return
        super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            event.accept()

    def shutdown(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel.set()
            # Native token exchange/GET each use 10s; cancellation gates the next operation.
            self.worker.wait(11000)
