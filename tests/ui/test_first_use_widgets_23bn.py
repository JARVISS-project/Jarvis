import json
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialogButtonBox

from jarvis.application.first_use import FirstUseService
from jarvis.security.permissions import PermissionStore
from tests.runtime.test_hardware_inventory_23bn import report
from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle


def test_first_use_auto_navigation_skip_resume_persistence_and_help_no_effects(tmp_path, monkeypatch):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        page = window.help_page
        def forbidden(*args, **kwargs):
            raise AssertionError('Help must not execute a Tool/provider')
        monkeypatch.setattr(h.tool_executor, 'execute', forbidden)
        monkeypatch.setattr(h.llm, 'generate', forbidden)
        settle(app, lambda: page.data and not page.busy)
        assert window.page_stack.currentWidget() is page
        assert page.title.text().startswith('1/6')
        page.next.click()
        settle(app, lambda: not page.busy)
        assert page.title.text().startswith('2/6')
        page.skip.click()
        settle(app, lambda: not page.busy)
        assert 'pausado' in page.status.text()
        assert not FirstUseService(service).progress()['auto_show']
        window._show_page('Conversa')
        window.nav_buttons['Ajuda'].click()
        settle(app, lambda: not page.busy)
        page.resume.click()
        settle(app, lambda: not page.busy)
        assert page.title.text().startswith('2/6')
        page.back.click()
        settle(app, lambda: not page.busy)
        page.open_page.click()
        assert window.page_stack.currentWidget() is window.preferences_page
        window.nav_buttons['Ajuda'].click()
        settle(app, lambda: not page.busy)
        page.search.setText('agenda')
        assert page.examples.count() == 1
        assert 'precisa configurar' in page.examples.item(0).text()
        assert 'não executa' in page.example_text.toPlainText()
        page.search.setText('planejado inexistente')
        assert page.examples.count() == 0
        assert not h.memory_store.get_all()
        assert not service.config.settings_path.exists()
        assert not service.permissions.path.exists()
    finally:
        close_system(h, controller, window)


def test_diagnostic_responsive_failure_recovery_preview_cancel_apply_and_rollback(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        page = window.help_page
        settle(app, lambda: page.data and not page.busy)
        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start()
        def slow_probe(config):
            time.sleep(.15)
            return report(tmp_path)
        service.first_use.probe = slow_probe
        page.diagnose_button.click()
        assert page.busy
        settle(app, lambda: not page.busy and 'Leitura' in page.status.text())
        settle(app, lambda: not page.busy)
        timer.stop()
        assert len(ticks) >= 3
        assert 'RAM total' in page.diagnostic.toPlainText()
        assert not service.config.settings_path.exists()
        page.advanced_button.click()
        assert 'external_supported' in page.diagnostic.toPlainText()
        page.advanced_button.click()
        page.profiles.setCurrentIndex(page.profiles.findData('large-v3'))
        page.prepare_button.click()
        settle(app, lambda: page.confirmation is not None)
        assert not service.config.settings_path.exists()
        page.confirmation.reject()
        assert not service.config.settings_path.exists()
        page.prepare_button.click()
        settle(app, lambda: page.confirmation is not None)
        page.confirmation.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Yes).click()
        settle(app, lambda: not page.busy)
        assert service.config.stt_profile == 'large-v3'
        assert page.rollback_button.isEnabled()
        page.rollback_button.click()
        settle(app, lambda: not page.busy)
        assert service.config.stt_profile == 'medium'
        assert not service.config.google_calendar_write_enabled
        def failing(config):
            raise TimeoutError
        service.first_use.probe = failing
        page.diagnose_button.click()
        settle(app, lambda: not page.busy)
        assert 'indisponível' in page.status.text()
        service.first_use.probe = slow_probe
        page.diagnose_button.click()
        settle(app, lambda: not page.busy and service.first_use.latest['assistant']['status'] == 'ready')
    finally:
        close_system(h, controller, window)


def test_help_effective_permissions_reloaded_and_progress_migration(tmp_path):
    app, h, service, controller, window = window_system(tmp_path)
    try:
        # Existing installations with saved progress must not be forced through it again.
        service.first_use.path.write_text(json.dumps(dict(version=0, step='voice', completed=['welcome', 'hardware'], dismissed=True)))
        settle(app, lambda: window.help_page.data and not window.help_page.busy)
        assert window.page_stack.currentIndex() == 0
        window.nav_buttons['Ajuda'].click()
        page = window.help_page
        settle(app, lambda: not page.busy)
        assert page.title.text().startswith('3/6')
        page.search.setText('Documentos locais')
        assert 'disponível no escopo' in page.examples.item(0).text()
        window.nav_buttons['Permissões'].click()
        permissions = window.permissions_page
        settle(app, lambda: not permissions.busy)
        permissions.search.setText('read_file')
        permissions.mode.setCurrentIndex(permissions.mode.findData('block'))
        permissions.apply_button.click()
        settle(app, lambda: not permissions.busy)
        window.nav_buttons['Ajuda'].click()
        settle(app, lambda: not page.busy)
        assert 'precisa configurar' in page.examples.item(0).text()
        assert PermissionStore(service.permissions.path, (tmp_path,)).decision('tool:read_file').mode == 'block'
        assert service.first_use.progress()['completed'] == ['welcome', 'hardware']
    finally:
        close_system(h, controller, window)
