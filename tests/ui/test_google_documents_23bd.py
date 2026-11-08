import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from threading import Event
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from jarvis.config import JarvisConfig
from jarvis.integrations.google.drive_docs.picker import LocalDocumentAuthorizations
from jarvis.ui.google_documents import GoogleDocumentsDialog
from jarvis.ui.qt_app import ControlCenterWindow


def app():
    return QApplication.instance() or QApplication([])


def test_entry_and_dialog_never_open_browser_on_construction(tmp_path, monkeypatch):
    app()
    calls = []
    config = JarvisConfig(settings_path=tmp_path/'settings.json', audit_log_path=tmp_path/'audit.log')
    picker = SimpleNamespace(local=LocalDocumentAuthorizations(config),
                             select=lambda *_a, **_k: calls.append('select'),
                             disconnect_local=lambda: calls.append('disconnect'))
    dialog = GoogleDocumentsDialog(picker=picker, config=config)
    try:
        assert dialog.select_button.text() == 'Selecionar documentos Google'
        assert not calls
        dialog.disconnect_button.click()
        assert calls == ['disconnect']
    finally:
        dialog.close()


def test_explicit_button_starts_worker_once_and_cancellation_keeps_owner(tmp_path, monkeypatch):
    app()
    config = JarvisConfig(settings_path=tmp_path/'settings.json', audit_log_path=tmp_path/'audit.log')
    local = LocalDocumentAuthorizations(config)
    config._write_local_settings({'google_drive_docs_client_config': str(tmp_path/'desktop.json')})
    entered, release = Event(), Event()
    calls = []
    def select(path, cancel):
        calls.append(path)
        entered.set()
        assert release.wait(2)
        assert cancel.is_set()
        return 'Cancelado.'
    dialog = GoogleDocumentsDialog(picker=SimpleNamespace(local=local, select=select), config=config)
    try:
        assert not calls
        dialog.select_button.click()
        assert entered.wait(2)
        dialog.select_documents()
        assert calls == [tmp_path/'desktop.json']
        assert not dialog.select_button.isEnabled()
        dialog.reject()
        assert dialog.worker.cancel.is_set()
        release.set()
        assert dialog.worker.wait(2000)
        app().processEvents()
        assert dialog.select_button.isEnabled()
        assert not dialog.worker.isRunning()
    finally:
        release.set()
        dialog.shutdown()
        dialog.close()


def test_file_dialog_cancel_has_zero_select_calls(tmp_path, monkeypatch):
    app()
    monkeypatch.delenv('JARVIS_GOOGLE_DRIVE_DOCS_CLIENT_CONFIG', raising=False)
    config = JarvisConfig(settings_path=tmp_path/'settings.json', audit_log_path=tmp_path/'audit.log')
    calls = []
    monkeypatch.setattr('jarvis.ui.google_documents.QFileDialog.getOpenFileName', lambda *_args: ('', ''))
    dialog = GoogleDocumentsDialog(picker=SimpleNamespace(local=LocalDocumentAuthorizations(config),
                                                        select=lambda *_a: calls.append(True)), config=config)
    try:
        dialog.select_button.click()
        assert calls == [] and dialog.worker is None
    finally:
        dialog.close()


def test_control_center_exposes_explicit_picker_entry(monkeypatch):
    app()
    calls = []
    from jarvis.ui import google_documents
    monkeypatch.setattr(google_documents, 'GoogleDocumentsDialog',
                        lambda parent: SimpleNamespace(exec=lambda: calls.append(parent)))
    control = ControlCenterWindow()
    try:
        assert calls == []
        control.google_documents_button.click()
        assert calls == [control]
    finally:
        control.close()
